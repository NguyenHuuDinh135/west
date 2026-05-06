# Lab: Xây dựng Microservice Scalable với DynamoDB + ElastiCache (Redis) + ECS Fargate

> **Mục tiêu**: Triển khai một microservice production-grade theo kiến trúc cache-aside, sử dụng DynamoDB làm primary store, ElastiCache Redis làm cache layer, chạy trên ECS Fargate trong VPC private subnet với security & observability đầy đủ.

---

## 1. Tổng quan

### 1.1. Use case

Microservice `product-api` cung cấp REST API truy vấn thông tin sản phẩm. Đặc tính tải:
- Read-heavy (~95% read, 5% write)
- Hot key tồn tại (một số sản phẩm được truy vấn liên tục)
- Latency yêu cầu p99 < 20ms
- Có thể tận dụng cho các use case khác (vd: PM2.5 sensor metadata, AWS exam question lookup, v.v.)

### 1.2. Kiến trúc tổng quan

```
                    Internet
                       │
                  ┌────▼─────┐
                  │   ALB    │  (Public subnets, 2 AZ)
                  └────┬─────┘
                       │ HTTP :80
              ┌────────▼────────┐
              │  ECS Fargate    │  (Private subnets, 2 AZ — KHÔNG có NAT)
              │  product-api    │
              │  (2..N tasks)   │
              └────┬────────────┘
                   │
                   │ Tất cả traffic AWS đi qua VPC Endpoints (PrivateLink)
                   │
        ┌──────────┼──────────────────────────────────────────────┐
        │          │                                              │
        ▼          ▼                                              ▼
  ┌──────────┐  ┌─────────────────────────────────┐  ┌────────────────────────┐
  │ Redis    │  │  Interface Endpoints (TLS 443) │  │  Gateway Endpoints      │
  │ 6379 TLS │  │  • ecr.api  • ecr.dkr          │  │  • DynamoDB (free)      │
  │ Multi-AZ │  │  • logs     • secretsmanager   │  │  • S3 — ECR layers (free)│
  └──────────┘  └─────────────────────────────────┘  └────────────────────────┘
```

### 1.3. Tại sao chọn các thành phần này

| Thành phần | Lý do |
|---|---|
| **DynamoDB** | Single-digit ms latency, scale tự động, phù hợp key-value/document, không cần quản lý hạ tầng |
| **ElastiCache Redis** | Giảm cost & latency cho hot key, hỗ trợ data structures phức tạp, Multi-AZ failover |
| **ECS Fargate** | Serverless container, không quản lý EC2, tích hợp sâu IAM/VPC/CloudWatch |
| **ALB** | Layer 7 routing, health checks, native ECS integration |
| **VPC Endpoints (PrivateLink)** | ECS pull image từ ECR, ghi log, đọc secrets, query DDB hoàn toàn trong AWS network — **không cần NAT Gateway**, không có public Internet egress, traffic không rời backbone AWS |

### 1.4. ElastiCache vs DAX — khi nào dùng cái nào?

| Tiêu chí | ElastiCache (Redis) | DynamoDB DAX |
|---|---|---|
| Phạm vi cache | Bất kỳ data nào (DynamoDB, RDS, kết quả tính toán) | Chỉ DynamoDB |
| Code change | Có (cache-aside pattern) | Tối thiểu (drop-in client) |
| Data structures | String, Hash, List, Set, Sorted Set, Stream | Item/Query/Scan results |
| Write-through | Không tự động (phải code) | Có sẵn |
| Cost | Linh hoạt (nhiều instance type) | Chỉ ràng buộc với DynamoDB |
| Use case khác | Session store, leaderboard, pub/sub, rate limiting | Chỉ tăng tốc DynamoDB |

→ **Lab này chọn ElastiCache Redis** vì tính linh hoạt cao, dễ tái sử dụng cho session/queue/leaderboard sau này.

### 1.5. Thời gian & chi phí ước tính

- **Thời gian thực hiện**: 2.5 — 3 giờ
- **Chi phí ước tính (ap-southeast-1, để chạy 24h)**:
  - ElastiCache `cache.t4g.micro` × 2 node (Multi-AZ): ~$0.40
  - ECS Fargate (0.25 vCPU, 0.5GB) × 2 task: ~$0.30
  - ALB: ~$0.55
  - **VPC Interface Endpoints** × 4 (ecr.api, ecr.dkr, logs, secretsmanager) × 2 AZ: ~$1.92 (~$0.01/h/AZ × 4 endpoint × 2 AZ × 24h)
  - VPC Gateway Endpoints (DynamoDB, S3): **Miễn phí**
  - DynamoDB On-Demand: <$0.01 (workload nhỏ)
  - **Tổng ~ $3.20/ngày**

> 💰 **So sánh với NAT Gateway**: NAT cố định $0.045/h × 24 = $1.10/ngày + $0.045/GB data processed. Với 4 interface endpoints chi phí cao hơn NAT một chút trong lab nhỏ, nhưng:
> - Production traffic lớn → endpoint thắng (NAT phí data ~$0.045/GB cộng dồn nhanh)
> - Endpoint không có **single point of failure** ở 1 AZ như NAT (single-AZ NAT)
> - Endpoint có **security boundary tốt hơn** — endpoint policy giới hạn được resource cụ thể
> - Có thể **giảm endpoints** nếu không bật multi-AZ private subnet (chỉ 1 AZ → tiết kiệm 50%)

> ⚠️ **Quan trọng**: Phải cleanup đúng thứ tự ở Module 9 để tránh cost ngoài ý muốn.

---

## 2. Prerequisites

```bash
# Kiểm tra tools
aws --version          # >= 2.15
docker --version       # >= 24
jq --version

# Cấu hình AWS CLI với region
export AWS_REGION=ap-southeast-1   # Đà Nẵng → Singapore là gần nhất
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export PROJECT=ddb-cache-lab
```

**IAM permissions cần thiết** cho user thực hiện lab: `AdministratorAccess` (lab environment). Trong production phải dùng least-privilege policy.

---

## 3. Module 1 — VPC & Networking

### 3.1. Best practice networking

- **2 AZ trở lên** cho HA
- **Private subnets** cho compute và cache (không expose Internet)
- **Public subnets** chỉ chứa ALB
- **KHÔNG dùng NAT Gateway** — toàn bộ AWS API traffic đi qua VPC endpoints
- **VPC Gateway Endpoints** (free): DynamoDB, S3 (S3 bắt buộc cho ECR image layers!)
- **VPC Interface Endpoints** ($0.01/h/AZ): ECR API, ECR Docker, CloudWatch Logs, Secrets Manager
- **Endpoint policies** giới hạn resource cụ thể (least privilege ngay tầng network)
- **Flow Logs** bật để forensic

### 3.2. Tại sao cần đủ các endpoint này để pull ECR image?

Khi ECS task pull image từ ECR, có 3 luồng traffic:

| Bước | Service | Endpoint cần |
|---|---|---|
| 1. Authentication (`GetAuthorizationToken`) | ECR API | `ecr.api` interface endpoint |
| 2. Manifest & metadata | ECR Docker | `ecr.dkr` interface endpoint |
| 3. **Tải image layers thực tế** | **S3** (ECR lưu layers ở S3) | **S3 Gateway endpoint** |

→ **Thiếu S3 Gateway endpoint = pull image fail** dù đã có ECR endpoints. Đây là pitfall hay gặp.

Ngoài ra:
- **CloudWatch Logs endpoint**: cho `awslogs` driver gửi container log
- **Secrets Manager endpoint**: cho Fargate inject `REDIS_AUTH_TOKEN` từ Secrets Manager

### 3.3. Triển khai bằng CloudFormation

Tạo file `01-network.yaml`:

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: VPC with public/private subnets across 2 AZ, no NAT Gateway, all egress via VPC endpoints

Parameters:
  ProjectName:
    Type: String
    Default: ddb-cache-lab

Resources:
  VPC:
    Type: AWS::EC2::VPC
    Properties:
      CidrBlock: 10.20.0.0/16
      EnableDnsHostnames: true   # BẮT BUỘC cho Interface Endpoint Private DNS
      EnableDnsSupport: true     # BẮT BUỘC cho Interface Endpoint Private DNS
      Tags: [{Key: Name, Value: !Sub "${ProjectName}-vpc"}]

  IGW:
    Type: AWS::EC2::InternetGateway
    Properties:
      Tags: [{Key: Name, Value: !Sub "${ProjectName}-igw"}]
  AttachIGW:
    Type: AWS::EC2::VPCGatewayAttachment
    Properties: {VpcId: !Ref VPC, InternetGatewayId: !Ref IGW}

  # === Public subnets — chỉ chứa ALB ===
  PublicSubnetA:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref VPC
      CidrBlock: 10.20.1.0/24
      AvailabilityZone: !Select [0, !GetAZs '']
      MapPublicIpOnLaunch: true
      Tags: [{Key: Name, Value: !Sub "${ProjectName}-public-a"}]
  PublicSubnetB:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref VPC
      CidrBlock: 10.20.2.0/24
      AvailabilityZone: !Select [1, !GetAZs '']
      MapPublicIpOnLaunch: true
      Tags: [{Key: Name, Value: !Sub "${ProjectName}-public-b"}]

  # === Private subnets — KHÔNG có route ra Internet ===
  PrivateSubnetA:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref VPC
      CidrBlock: 10.20.11.0/24
      AvailabilityZone: !Select [0, !GetAZs '']
      Tags: [{Key: Name, Value: !Sub "${ProjectName}-private-a"}]
  PrivateSubnetB:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref VPC
      CidrBlock: 10.20.12.0/24
      AvailabilityZone: !Select [1, !GetAZs '']
      Tags: [{Key: Name, Value: !Sub "${ProjectName}-private-b"}]

  # === Public route table — có route ra IGW ===
  PublicRT:
    Type: AWS::EC2::RouteTable
    Properties: {VpcId: !Ref VPC}
  PublicDefaultRoute:
    Type: AWS::EC2::Route
    DependsOn: AttachIGW
    Properties:
      RouteTableId: !Ref PublicRT
      DestinationCidrBlock: 0.0.0.0/0
      GatewayId: !Ref IGW
  PublicRTAssocA:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties: {RouteTableId: !Ref PublicRT, SubnetId: !Ref PublicSubnetA}
  PublicRTAssocB:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties: {RouteTableId: !Ref PublicRT, SubnetId: !Ref PublicSubnetB}

  # === Private route table — KHÔNG có route 0.0.0.0/0 ===
  # Chỉ có local route mặc định + Gateway Endpoints (DDB, S3) tự thêm route prefix list
  PrivateRT:
    Type: AWS::EC2::RouteTable
    Properties: {VpcId: !Ref VPC}
  PrivateRTAssocA:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties: {RouteTableId: !Ref PrivateRT, SubnetId: !Ref PrivateSubnetA}
  PrivateRTAssocB:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties: {RouteTableId: !Ref PrivateRT, SubnetId: !Ref PrivateSubnetB}

  # ============================================================
  # GATEWAY ENDPOINTS (FREE) — chỉ thêm route vào route table
  # ============================================================

  # DynamoDB Gateway Endpoint
  DynamoDBEndpoint:
    Type: AWS::EC2::VPCEndpoint
    Properties:
      VpcId: !Ref VPC
      ServiceName: !Sub "com.amazonaws.${AWS::Region}.dynamodb"
      VpcEndpointType: Gateway
      RouteTableIds: [!Ref PrivateRT]
      PolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal: "*"
            Action:
              - dynamodb:GetItem
              - dynamodb:PutItem
              - dynamodb:UpdateItem
              - dynamodb:DeleteItem
              - dynamodb:Query
              - dynamodb:BatchGetItem
              - dynamodb:BatchWriteItem
              - dynamodb:DescribeTable
            Resource: !Sub "arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${ProjectName}-*"

  # S3 Gateway Endpoint — BẮT BUỘC để pull ECR image (layers lưu ở S3)
  S3Endpoint:
    Type: AWS::EC2::VPCEndpoint
    Properties:
      VpcId: !Ref VPC
      ServiceName: !Sub "com.amazonaws.${AWS::Region}.s3"
      VpcEndpointType: Gateway
      RouteTableIds: [!Ref PrivateRT]
      # Endpoint policy giới hạn chỉ truy cập S3 buckets của ECR + bucket riêng nếu cần
      PolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Sid: AllowEcrLayerPull
            Effect: Allow
            Principal: "*"
            Action:
              - s3:GetObject
            Resource:
              # ECR lưu image ở các bucket có prefix "prod-${region}-starport-layer-bucket"
              - !Sub "arn:aws:s3:::prod-${AWS::Region}-starport-layer-bucket/*"

  # ============================================================
  # INTERFACE ENDPOINTS (PrivateLink) — $0.01/h/AZ + $0.01/GB
  # ============================================================

  # SG cho tất cả interface endpoints — chỉ cho 443 từ trong VPC
  EndpointsSG:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: Allow HTTPS to VPC interface endpoints from within VPC
      VpcId: !Ref VPC
      SecurityGroupIngress:
        - IpProtocol: tcp
          FromPort: 443
          ToPort: 443
          CidrIp: 10.20.0.0/16
      Tags: [{Key: Name, Value: !Sub "${ProjectName}-endpoints-sg"}]

  # ECR API Endpoint — cho GetAuthorizationToken, DescribeImages, etc.
  EcrApiEndpoint:
    Type: AWS::EC2::VPCEndpoint
    Properties:
      VpcId: !Ref VPC
      ServiceName: !Sub "com.amazonaws.${AWS::Region}.ecr.api"
      VpcEndpointType: Interface
      PrivateDnsEnabled: true       # AWS SDK tự động resolve về endpoint
      SubnetIds: [!Ref PrivateSubnetA, !Ref PrivateSubnetB]
      SecurityGroupIds: [!Ref EndpointsSG]

  # ECR Docker Endpoint — cho docker pull (manifest, blob)
  EcrDkrEndpoint:
    Type: AWS::EC2::VPCEndpoint
    Properties:
      VpcId: !Ref VPC
      ServiceName: !Sub "com.amazonaws.${AWS::Region}.ecr.dkr"
      VpcEndpointType: Interface
      PrivateDnsEnabled: true
      SubnetIds: [!Ref PrivateSubnetA, !Ref PrivateSubnetB]
      SecurityGroupIds: [!Ref EndpointsSG]

  # CloudWatch Logs Endpoint — cho awslogs driver
  LogsEndpoint:
    Type: AWS::EC2::VPCEndpoint
    Properties:
      VpcId: !Ref VPC
      ServiceName: !Sub "com.amazonaws.${AWS::Region}.logs"
      VpcEndpointType: Interface
      PrivateDnsEnabled: true
      SubnetIds: [!Ref PrivateSubnetA, !Ref PrivateSubnetB]
      SecurityGroupIds: [!Ref EndpointsSG]

  # Secrets Manager Endpoint — cho Fargate inject secrets
  SecretsManagerEndpoint:
    Type: AWS::EC2::VPCEndpoint
    Properties:
      VpcId: !Ref VPC
      ServiceName: !Sub "com.amazonaws.${AWS::Region}.secretsmanager"
      VpcEndpointType: Interface
      PrivateDnsEnabled: true
      SubnetIds: [!Ref PrivateSubnetA, !Ref PrivateSubnetB]
      SecurityGroupIds: [!Ref EndpointsSG]

  # ============================================================
  # VPC FLOW LOGS
  # ============================================================
  FlowLogsGroup:
    Type: AWS::Logs::LogGroup
    Properties:
      LogGroupName: !Sub "/aws/vpc/${ProjectName}/flowlogs"
      RetentionInDays: 7
  FlowLogsRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal: {Service: vpc-flow-logs.amazonaws.com}
            Action: sts:AssumeRole
      Policies:
        - PolicyName: write-logs
          PolicyDocument:
            Statement:
              - Effect: Allow
                Action:
                  - logs:CreateLogStream
                  - logs:PutLogEvents
                  - logs:DescribeLogGroups
                  - logs:DescribeLogStreams
                Resource: !GetAtt FlowLogsGroup.Arn
  FlowLog:
    Type: AWS::EC2::FlowLog
    Properties:
      ResourceId: !Ref VPC
      ResourceType: VPC
      TrafficType: REJECT
      LogDestination: !GetAtt FlowLogsGroup.Arn
      LogDestinationType: cloud-watch-logs
      DeliverLogsPermissionArn: !GetAtt FlowLogsRole.Arn

Outputs:
  VpcId: {Value: !Ref VPC, Export: {Name: !Sub "${ProjectName}-vpc-id"}}
  PrivateSubnetA: {Value: !Ref PrivateSubnetA, Export: {Name: !Sub "${ProjectName}-private-a"}}
  PrivateSubnetB: {Value: !Ref PrivateSubnetB, Export: {Name: !Sub "${ProjectName}-private-b"}}
  PublicSubnetA: {Value: !Ref PublicSubnetA, Export: {Name: !Sub "${ProjectName}-public-a"}}
  PublicSubnetB: {Value: !Ref PublicSubnetB, Export: {Name: !Sub "${ProjectName}-public-b"}}
  EndpointsSG:   {Value: !Ref EndpointsSG,   Export: {Name: !Sub "${ProjectName}-endpoints-sg"}}
```

Deploy:
```bash
aws cloudformation deploy \
  --template-file 01-network.yaml \
  --stack-name ${PROJECT}-network \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides ProjectName=${PROJECT}
```

### 3.4. Verify endpoints hoạt động

```bash
# Liệt kê tất cả endpoints
aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=$(aws cloudformation list-exports \
            --query "Exports[?Name=='${PROJECT}-vpc-id'].Value" --output text)" \
  --query 'VpcEndpoints[].[VpcEndpointType,ServiceName,State]' \
  --output table

# Kết quả mong đợi:
# - 2 Gateway endpoints (DynamoDB, S3) → state "available"
# - 4 Interface endpoints (ecr.api, ecr.dkr, logs, secretsmanager) → state "available"
```

> 💡 **Best practice insights**:
>
> 1. **`PrivateDnsEnabled: true`** là chìa khóa — khi bật, các record DNS như `ecr.ap-southeast-1.amazonaws.com` sẽ được resolve thành private IP của endpoint **mà không cần thay đổi code**. Boto3, ECS Fargate, awslogs driver tất cả đều "just work".
>
> 2. **Endpoint policy tách biệt với IAM policy** — đây là defense in depth. Ngay cả khi IAM role bị compromise, endpoint policy vẫn chặn được truy cập ngoài resource cho phép.
>
> 3. **S3 Gateway Endpoint có endpoint policy riêng** — chỉ cho phép `GetObject` đến bucket ECR layer. Như vậy nếu app code bị compromise cũng không thể đọc/ghi tùy tiện vào S3 buckets khác.
>
> 4. **Tại sao bắt buộc S3 endpoint cho ECR?** Image manifest đi qua `ecr.dkr`, nhưng các blob layers (chiếm > 99% dung lượng) được lưu trữ thực tế ở S3 và Docker daemon download trực tiếp từ S3 URL có signed token. Thiếu S3 endpoint = pull bị stuck ở "Pulling fs layer".
>
> 5. **Interface endpoint chỉ resolve trong AZ tương ứng** — phải tạo trong tất cả AZ mà ECS task có thể chạy, không thì task ở AZ thiếu endpoint sẽ fail.

---

## 4. Module 2 — DynamoDB Table

### 4.1. Best practice thiết kế bảng

- **Partition key thiết kế đều**: tránh hot partition (đừng dùng timestamp/sequential ID làm PK)
- **Composite key (PK+SK)** khi cần truy vấn theo range
- **On-Demand mode** cho workload chưa rõ pattern; chuyển sang Provisioned + Auto Scaling khi đã ổn định
- **Point-in-Time Recovery (PITR)**: bật ngay từ đầu — backup liên tục, không tốn nhiều
- **Encryption at rest** với AWS-managed KMS (mặc định) hoặc Customer-managed KMS (compliance cao hơn)
- **TTL** cho dữ liệu có vòng đời (cache, session, log)
- **Streams** bật nếu cần CDC (sang OpenSearch, Lambda, EventBridge)
- **Contributor Insights** để phát hiện hot key
- **Deletion Protection** cho production table

### 4.2. Schema cho `products` table

| Attribute | Type | Vai trò |
|---|---|---|
| `pk` | String | Partition key — `PRODUCT#<category>` |
| `sk` | String | Sort key — `SKU#<sku_id>` |
| `name` | String | Tên sản phẩm |
| `price` | Number | Giá |
| `stock` | Number | Tồn kho |
| `updated_at` | String | ISO timestamp |
| `gsi1pk` | String | GSI1 partition key — `STATUS#<status>` (cho query theo trạng thái) |
| `gsi1sk` | String | GSI1 sort key — `PRICE#<padded_price>` |

### 4.3. Tạo bảng

```bash
aws dynamodb create-table \
  --table-name ${PROJECT}-products \
  --attribute-definitions \
      AttributeName=pk,AttributeType=S \
      AttributeName=sk,AttributeType=S \
      AttributeName=gsi1pk,AttributeType=S \
      AttributeName=gsi1sk,AttributeType=S \
  --key-schema \
      AttributeName=pk,KeyType=HASH \
      AttributeName=sk,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --global-secondary-indexes \
      'IndexName=gsi1,KeySchema=[{AttributeName=gsi1pk,KeyType=HASH},{AttributeName=gsi1sk,KeyType=RANGE}],Projection={ProjectionType=ALL}' \
  --sse-specification Enabled=true,SSEType=KMS \
  --stream-specification StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES \
  --deletion-protection-enabled \
  --tags Key=Project,Value=${PROJECT}

# Bật PITR
aws dynamodb update-continuous-backups \
  --table-name ${PROJECT}-products \
  --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true

# Bật Contributor Insights (phát hiện hot key)
aws dynamodb update-contributor-insights \
  --table-name ${PROJECT}-products \
  --contributor-insights-action ENABLE
```

### 4.4. Seed dữ liệu mẫu

```bash
cat > seed.json <<'EOF'
{
  "ddb-cache-lab-products": [
    {"PutRequest": {"Item": {"pk": {"S": "PRODUCT#electronics"}, "sk": {"S": "SKU#E001"}, "name": {"S": "Air Quality Sensor PM2.5"}, "price": {"N": "1290000"}, "stock": {"N": "42"}, "gsi1pk": {"S": "STATUS#active"}, "gsi1sk": {"S": "PRICE#0001290000"}, "updated_at": {"S": "2026-05-06T08:00:00Z"}}}},
    {"PutRequest": {"Item": {"pk": {"S": "PRODUCT#electronics"}, "sk": {"S": "SKU#E002"}, "name": {"S": "Raspberry Pi 5 8GB"}, "price": {"N": "2490000"}, "stock": {"N": "15"}, "gsi1pk": {"S": "STATUS#active"}, "gsi1sk": {"S": "PRICE#0002490000"}, "updated_at": {"S": "2026-05-06T08:00:00Z"}}}},
    {"PutRequest": {"Item": {"pk": {"S": "PRODUCT#books"}, "sk": {"S": "SKU#B001"}, "name": {"S": "Designing Data-Intensive Applications"}, "price": {"N": "650000"}, "stock": {"N": "8"}, "gsi1pk": {"S": "STATUS#active"}, "gsi1sk": {"S": "PRICE#0000650000"}, "updated_at": {"S": "2026-05-06T08:00:00Z"}}}}
  ]
}
EOF

aws dynamodb batch-write-item --request-items file://seed.json
```

> 💡 **Best practice insight**: GSI1 dùng pattern overloaded index — một GSI duy nhất phục vụ nhiều truy vấn khác nhau bằng cách thay đổi giá trị `gsi1pk`/`gsi1sk` theo từng entity. Đây là pattern single-table design của Rick Houlihan.

---

## 5. Module 3 — ElastiCache Redis (Multi-AZ)

### 5.1. Best practice cho Redis production

- **Replication group** với ít nhất 1 replica ở AZ khác → automatic failover
- **Encryption in transit (TLS)** + **Encryption at rest** → bắt buộc cho data nhạy cảm
- **AUTH token** lưu trong Secrets Manager (đừng hardcode)
- **Parameter group tuned**: `maxmemory-policy = allkeys-lru` cho cache pure
- **Auto minor version upgrade** = true
- **Snapshot retention** ít nhất 1 ngày
- **CloudWatch alarms** cho `EngineCPUUtilization`, `DatabaseMemoryUsagePercentage`, `Evictions`
- **Cluster mode** chỉ bật khi data > 1 node memory hoặc cần > 100k ops/sec/node

### 5.2. Tạo Security Groups

Tạo file `02-securitygroups.yaml`:

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Parameters:
  ProjectName: {Type: String, Default: ddb-cache-lab}

Resources:
  AlbSG:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: ALB ingress from Internet
      VpcId: {Fn::ImportValue: !Sub "${ProjectName}-vpc-id"}
      SecurityGroupIngress:
        - IpProtocol: tcp
          FromPort: 80
          ToPort: 80
          CidrIp: 0.0.0.0/0
      Tags: [{Key: Name, Value: !Sub "${ProjectName}-alb-sg"}]

  EcsSG:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: ECS tasks — only accept from ALB
      VpcId: {Fn::ImportValue: !Sub "${ProjectName}-vpc-id"}
      Tags: [{Key: Name, Value: !Sub "${ProjectName}-ecs-sg"}]

  EcsIngressFromAlb:
    Type: AWS::EC2::SecurityGroupIngress
    Properties:
      GroupId: !Ref EcsSG
      IpProtocol: tcp
      FromPort: 8000
      ToPort: 8000
      SourceSecurityGroupId: !Ref AlbSG

  RedisSG:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: Redis — only accept from ECS
      VpcId: {Fn::ImportValue: !Sub "${ProjectName}-vpc-id"}
      Tags: [{Key: Name, Value: !Sub "${ProjectName}-redis-sg"}]

  RedisIngressFromEcs:
    Type: AWS::EC2::SecurityGroupIngress
    Properties:
      GroupId: !Ref RedisSG
      IpProtocol: tcp
      FromPort: 6379
      ToPort: 6379
      SourceSecurityGroupId: !Ref EcsSG

Outputs:
  AlbSG: {Value: !Ref AlbSG, Export: {Name: !Sub "${ProjectName}-alb-sg"}}
  EcsSG: {Value: !Ref EcsSG, Export: {Name: !Sub "${ProjectName}-ecs-sg"}}
  RedisSG: {Value: !Ref RedisSG, Export: {Name: !Sub "${ProjectName}-redis-sg"}}
```

```bash
aws cloudformation deploy \
  --template-file 02-securitygroups.yaml \
  --stack-name ${PROJECT}-sg \
  --parameter-overrides ProjectName=${PROJECT}
```

> ⚠️ **Nguyên tắc least-privilege**: Redis SG **chỉ** cho phép từ ECS SG, không từ CIDR. Như vậy khi compromise một subnet khác cũng không truy cập được Redis.

### 5.3. Lưu Redis AUTH token vào Secrets Manager

```bash
REDIS_AUTH=$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-40)

aws secretsmanager create-secret \
  --name ${PROJECT}/redis/auth-token \
  --description "Redis AUTH token" \
  --secret-string "{\"authToken\":\"${REDIS_AUTH}\"}"
```

### 5.4. Tạo Subnet Group + Replication Group

```bash
# Subnet group
aws elasticache create-cache-subnet-group \
  --cache-subnet-group-name ${PROJECT}-redis-subnet \
  --cache-subnet-group-description "Private subnets for Redis" \
  --subnet-ids \
      $(aws cloudformation list-exports --query "Exports[?Name=='${PROJECT}-private-a'].Value" --output text) \
      $(aws cloudformation list-exports --query "Exports[?Name=='${PROJECT}-private-b'].Value" --output text)

# Replication group: 1 primary + 1 replica, Multi-AZ
REDIS_SG=$(aws cloudformation list-exports --query "Exports[?Name=='${PROJECT}-redis-sg'].Value" --output text)

aws elasticache create-replication-group \
  --replication-group-id ${PROJECT}-redis \
  --replication-group-description "Cache for product-api" \
  --engine redis \
  --engine-version 7.1 \
  --cache-node-type cache.t4g.micro \
  --num-cache-clusters 2 \
  --automatic-failover-enabled \
  --multi-az-enabled \
  --cache-subnet-group-name ${PROJECT}-redis-subnet \
  --security-group-ids ${REDIS_SG} \
  --transit-encryption-enabled \
  --at-rest-encryption-enabled \
  --auth-token "${REDIS_AUTH}" \
  --snapshot-retention-limit 1 \
  --auto-minor-version-upgrade \
  --tags Key=Project,Value=${PROJECT}
```

Đợi ~8-10 phút cho Redis sẵn sàng:

```bash
aws elasticache wait replication-group-available \
  --replication-group-id ${PROJECT}-redis

REDIS_ENDPOINT=$(aws elasticache describe-replication-groups \
  --replication-group-id ${PROJECT}-redis \
  --query 'ReplicationGroups[0].NodeGroups[0].PrimaryEndpoint.Address' \
  --output text)
echo "Redis endpoint: ${REDIS_ENDPOINT}"
```

---

## 6. Module 4 — Application Code (Cache-Aside Pattern)

### 6.1. Cache-aside pattern là gì?

```
Client → API → 1. Check Redis (GET cache_key)
                  ├─ HIT  → return cached value
                  └─ MISS → 2. Query DynamoDB
                            3. SET cache_key with TTL
                            4. return value

Write  → API → 1. Write DynamoDB
                2. Invalidate cache (DEL cache_key)
                   (hoặc write-through tùy use case)
```

**Tradeoffs**:
- ✅ Cache chỉ chứa dữ liệu thực sự được đọc → memory efficient
- ✅ Cache failure không làm sập app (graceful degradation)
- ⚠️ Stampede problem: nhiều request miss cùng lúc → query DB cùng lúc → cần singleflight/lock
- ⚠️ Stale data trong khoảng TTL → cần invalidation đúng

### 6.2. Sample app — Python FastAPI

`app/main.py`:

```python
import os, json, logging, asyncio
from contextlib import asynccontextmanager
from typing import Optional

import boto3
import redis.asyncio as redis
from botocore.config import Config
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("product-api")

TABLE_NAME = os.environ["TABLE_NAME"]
REDIS_HOST = os.environ["REDIS_HOST"]
REDIS_AUTH = os.environ["REDIS_AUTH_TOKEN"]
CACHE_TTL = int(os.environ.get("CACHE_TTL_SECONDS", "300"))
AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-1")

# Boto3 with retry & connection reuse
boto_cfg = Config(
    region_name=AWS_REGION,
    retries={"max_attempts": 3, "mode": "adaptive"},
    max_pool_connections=50,
)
ddb = boto3.resource("dynamodb", config=boto_cfg)
table = ddb.Table(TABLE_NAME)

# Redis client (TLS + AUTH)
redis_client: Optional[redis.Redis] = None

# Singleflight: tránh cache stampede
inflight: dict[str, asyncio.Future] = {}
inflight_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=6379,
        password=REDIS_AUTH,
        ssl=True,
        ssl_cert_reqs=None,  # ElastiCache uses AWS CA, simplified for lab
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=2,
        retry_on_timeout=True,
        health_check_interval=30,
    )
    try:
        await redis_client.ping()
        log.info("Connected to Redis")
    except Exception as e:
        log.error(f"Redis connect failed: {e}")
    yield
    await redis_client.aclose()


app = FastAPI(title="product-api", lifespan=lifespan)


class Product(BaseModel):
    category: str
    sku: str
    name: str
    price: float
    stock: int


def cache_key(category: str, sku: str) -> str:
    return f"product:{category}:{sku}"


async def get_from_cache(key: str) -> Optional[dict]:
    try:
        data = await redis_client.get(key)
        return json.loads(data) if data else None
    except Exception as e:
        log.warning(f"Cache GET failed (graceful degrade): {e}")
        return None


async def set_to_cache(key: str, value: dict, ttl: int = CACHE_TTL):
    try:
        await redis_client.setex(key, ttl, json.dumps(value, default=str))
    except Exception as e:
        log.warning(f"Cache SET failed: {e}")


async def invalidate_cache(key: str):
    try:
        await redis_client.delete(key)
    except Exception as e:
        log.warning(f"Cache DEL failed: {e}")


async def fetch_from_ddb(category: str, sku: str) -> Optional[dict]:
    resp = table.get_item(Key={"pk": f"PRODUCT#{category}", "sk": f"SKU#{sku}"})
    item = resp.get("Item")
    if not item:
        return None
    return {
        "category": category,
        "sku": sku,
        "name": item["name"],
        "price": float(item["price"]),
        "stock": int(item["stock"]),
    }


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    try:
        await redis_client.ping()
        table.load()
        return {"status": "ready"}
    except Exception as e:
        raise HTTPException(503, f"not ready: {e}")


@app.get("/products/{category}/{sku}")
async def get_product(category: str, sku: str):
    key = cache_key(category, sku)

    # 1. Cache lookup
    cached = await get_from_cache(key)
    if cached:
        log.info(f"cache HIT key={key}")
        return {"source": "cache", **cached}

    # 2. Singleflight để tránh stampede
    async with inflight_lock:
        if key in inflight:
            fut = inflight[key]
        else:
            fut = asyncio.get_event_loop().create_future()
            inflight[key] = fut
            asyncio.create_task(_load_and_set(category, sku, key, fut))

    item = await fut
    if not item:
        raise HTTPException(404, "Product not found")
    return {"source": "ddb", **item}


async def _load_and_set(category: str, sku: str, key: str, fut: asyncio.Future):
    try:
        log.info(f"cache MISS key={key} → DDB")
        item = await asyncio.to_thread(fetch_from_ddb, category, sku)
        if item:
            await set_to_cache(key, item)
        fut.set_result(item)
    except Exception as e:
        fut.set_exception(e)
    finally:
        async with inflight_lock:
            inflight.pop(key, None)


@app.put("/products/{category}/{sku}")
async def upsert_product(category: str, sku: str, p: Product):
    table.put_item(Item={
        "pk": f"PRODUCT#{category}",
        "sk": f"SKU#{sku}",
        "name": p.name,
        "price": int(p.price),
        "stock": p.stock,
        "gsi1pk": "STATUS#active",
        "gsi1sk": f"PRICE#{int(p.price):010d}",
    })
    # Cache invalidation (cache-aside)
    await invalidate_cache(cache_key(category, sku))
    return {"status": "ok"}
```

`app/requirements.txt`:
```
fastapi==0.115.4
uvicorn[standard]==0.32.0
boto3==1.35.50
redis==5.2.0
pydantic==2.9.2
```

`app/Dockerfile`:
```dockerfile
FROM public.ecr.aws/docker/library/python:3.12-slim AS base
WORKDIR /app

# Non-root user
RUN groupadd -r app && useradd -r -g app app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

USER app
EXPOSE 8000

# Health check (also defined in task definition)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz').read()" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

> 💡 **Best practice insight**:
> - **Singleflight pattern** ngăn cache stampede — khi N request cùng MISS, chỉ 1 request đi DB.
> - **Graceful degradation**: nếu Redis chết, app vẫn chạy (chỉ chậm hơn) chứ không sập.
> - **Connection pooling** cho cả Redis và boto3 → giảm overhead.
> - **Non-root container user** → security baseline.

---

## 7. Module 5 — Build & Push Image lên ECR

```bash
# Tạo ECR repo
aws ecr create-repository \
  --repository-name ${PROJECT}/product-api \
  --image-scanning-configuration scanOnPush=true \
  --image-tag-mutability IMMUTABLE \
  --encryption-configuration encryptionType=AES256

# Lifecycle policy: giữ 10 image gần nhất
aws ecr put-lifecycle-policy \
  --repository-name ${PROJECT}/product-api \
  --lifecycle-policy-text '{
    "rules": [{
      "rulePriority": 1,
      "selection": {"tagStatus":"any","countType":"imageCountMoreThan","countNumber":10},
      "action": {"type":"expire"}
    }]
  }'

# Login & push
aws ecr get-login-password --region ${AWS_REGION} | \
  docker login --username AWS --password-stdin \
  ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com

cd app/
IMAGE_TAG=v1.0.0
docker build -t ${PROJECT}/product-api:${IMAGE_TAG} .
docker tag ${PROJECT}/product-api:${IMAGE_TAG} \
  ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${PROJECT}/product-api:${IMAGE_TAG}
docker push \
  ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${PROJECT}/product-api:${IMAGE_TAG}
```

> 💡 **Best practice**: `imageTagMutability=IMMUTABLE` ngăn việc đè tag (vd: `latest` bị thay đổi giữa các deploy → khó debug). Mỗi deploy = 1 tag mới (semver hoặc git SHA).

---

## 8. Module 6 — ECS Cluster, Task Definition, Service

### 8.1. IAM Roles (separation of concerns)

- **Task Execution Role**: Fargate dùng để pull image, ghi log, đọc secrets injection. Không có quyền business.
- **Task Role**: container code dùng để gọi DynamoDB. Đây là principal của AWS SDK trong app.

`03-iam.yaml`:
```yaml
AWSTemplateFormatVersion: '2010-09-09'
Parameters:
  ProjectName: {Type: String, Default: ddb-cache-lab}

Resources:
  TaskExecutionRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: !Sub "${ProjectName}-task-exec"
      AssumeRolePolicyDocument:
        Statement:
          - Effect: Allow
            Principal: {Service: ecs-tasks.amazonaws.com}
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
      Policies:
        - PolicyName: read-secrets
          PolicyDocument:
            Statement:
              - Effect: Allow
                Action:
                  - secretsmanager:GetSecretValue
                Resource: !Sub "arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:${ProjectName}/*"

  TaskRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: !Sub "${ProjectName}-task-role"
      AssumeRolePolicyDocument:
        Statement:
          - Effect: Allow
            Principal: {Service: ecs-tasks.amazonaws.com}
            Action: sts:AssumeRole
      Policies:
        - PolicyName: ddb-access
          PolicyDocument:
            Statement:
              - Effect: Allow
                Action:
                  - dynamodb:GetItem
                  - dynamodb:PutItem
                  - dynamodb:UpdateItem
                  - dynamodb:DeleteItem
                  - dynamodb:Query
                  - dynamodb:BatchGetItem
                Resource:
                  - !Sub "arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${ProjectName}-products"
                  - !Sub "arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${ProjectName}-products/index/*"

Outputs:
  TaskExecRoleArn: {Value: !GetAtt TaskExecutionRole.Arn, Export: {Name: !Sub "${ProjectName}-task-exec-arn"}}
  TaskRoleArn:     {Value: !GetAtt TaskRole.Arn,         Export: {Name: !Sub "${ProjectName}-task-role-arn"}}
```

```bash
aws cloudformation deploy \
  --template-file 03-iam.yaml \
  --stack-name ${PROJECT}-iam \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides ProjectName=${PROJECT}
```

> 🔒 **Bài học bảo mật**: Phân tách rõ Task Execution Role (Fargate platform) và Task Role (app code). Đừng gộp một role làm cả hai — vi phạm least privilege.

### 8.2. CloudWatch Log Group

```bash
aws logs create-log-group --log-group-name /ecs/${PROJECT}/product-api
aws logs put-retention-policy \
  --log-group-name /ecs/${PROJECT}/product-api \
  --retention-in-days 14
```

### 8.3. Task Definition

```bash
SECRET_ARN=$(aws secretsmanager describe-secret --secret-id ${PROJECT}/redis/auth-token --query ARN --output text)
TASK_EXEC_ARN=$(aws cloudformation list-exports --query "Exports[?Name=='${PROJECT}-task-exec-arn'].Value" --output text)
TASK_ROLE_ARN=$(aws cloudformation list-exports --query "Exports[?Name=='${PROJECT}-task-role-arn'].Value" --output text)

cat > taskdef.json <<EOF
{
  "family": "${PROJECT}-product-api",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "runtimePlatform": {"operatingSystemFamily": "LINUX", "cpuArchitecture": "ARM64"},
  "executionRoleArn": "${TASK_EXEC_ARN}",
  "taskRoleArn": "${TASK_ROLE_ARN}",
  "containerDefinitions": [{
    "name": "product-api",
    "image": "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${PROJECT}/product-api:v1.0.0",
    "essential": true,
    "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
    "environment": [
      {"name": "TABLE_NAME", "value": "${PROJECT}-products"},
      {"name": "REDIS_HOST", "value": "${REDIS_ENDPOINT}"},
      {"name": "AWS_REGION", "value": "${AWS_REGION}"},
      {"name": "CACHE_TTL_SECONDS", "value": "300"}
    ],
    "secrets": [
      {"name": "REDIS_AUTH_TOKEN", "valueFrom": "${SECRET_ARN}:authToken::"}
    ],
    "healthCheck": {
      "command": ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz').read()\" || exit 1"],
      "interval": 30,
      "timeout": 5,
      "retries": 3,
      "startPeriod": 15
    },
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/${PROJECT}/product-api",
        "awslogs-region": "${AWS_REGION}",
        "awslogs-stream-prefix": "ecs"
      }
    },
    "readonlyRootFilesystem": false,
    "ulimits": [{"name": "nofile", "softLimit": 65536, "hardLimit": 65536}]
  }]
}
EOF

aws ecs register-task-definition --cli-input-json file://taskdef.json
```

> 💡 **Insight**:
> - `cpuArchitecture: ARM64` (Graviton) rẻ hơn x86 ~20% với cùng performance — đảm bảo build image cho ARM (`docker buildx build --platform linux/arm64`).
> - **Secrets injection** qua `secrets` field thay vì environment variable → secret không lộ trong `describe-tasks`.
> - **Health check** có `startPeriod=15s` để app kịp khởi động.

### 8.4. ALB

```bash
ALB_SG=$(aws cloudformation list-exports --query "Exports[?Name=='${PROJECT}-alb-sg'].Value" --output text)
PUBLIC_A=$(aws cloudformation list-exports --query "Exports[?Name=='${PROJECT}-public-a'].Value" --output text)
PUBLIC_B=$(aws cloudformation list-exports --query "Exports[?Name=='${PROJECT}-public-b'].Value" --output text)
VPC_ID=$(aws cloudformation list-exports --query "Exports[?Name=='${PROJECT}-vpc-id'].Value" --output text)

ALB_ARN=$(aws elbv2 create-load-balancer \
  --name ${PROJECT}-alb \
  --subnets ${PUBLIC_A} ${PUBLIC_B} \
  --security-groups ${ALB_SG} \
  --type application \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text)

TG_ARN=$(aws elbv2 create-target-group \
  --name ${PROJECT}-tg \
  --protocol HTTP --port 8000 \
  --vpc-id ${VPC_ID} \
  --target-type ip \
  --health-check-path /healthz \
  --health-check-interval-seconds 15 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3 \
  --matcher HttpCode=200 \
  --query 'TargetGroups[0].TargetGroupArn' --output text)

# Tăng deregistration delay xuống 30s (default 300s) cho dev
aws elbv2 modify-target-group-attributes \
  --target-group-arn ${TG_ARN} \
  --attributes Key=deregistration_delay.timeout_seconds,Value=30

aws elbv2 create-listener \
  --load-balancer-arn ${ALB_ARN} \
  --protocol HTTP --port 80 \
  --default-actions Type=forward,TargetGroupArn=${TG_ARN}
```

### 8.5. ECS Cluster + Service

```bash
aws ecs create-cluster \
  --cluster-name ${PROJECT}-cluster \
  --capacity-providers FARGATE FARGATE_SPOT \
  --default-capacity-provider-strategy capacityProvider=FARGATE,weight=1 \
  --settings name=containerInsights,value=enabled

ECS_SG=$(aws cloudformation list-exports --query "Exports[?Name=='${PROJECT}-ecs-sg'].Value" --output text)
PRIV_A=$(aws cloudformation list-exports --query "Exports[?Name=='${PROJECT}-private-a'].Value" --output text)
PRIV_B=$(aws cloudformation list-exports --query "Exports[?Name=='${PROJECT}-private-b'].Value" --output text)

aws ecs create-service \
  --cluster ${PROJECT}-cluster \
  --service-name product-api \
  --task-definition ${PROJECT}-product-api \
  --desired-count 2 \
  --launch-type FARGATE \
  --platform-version LATEST \
  --network-configuration "awsvpcConfiguration={subnets=[${PRIV_A},${PRIV_B}],securityGroups=[${ECS_SG}],assignPublicIp=DISABLED}" \
  --load-balancers "targetGroupArn=${TG_ARN},containerName=product-api,containerPort=8000" \
  --health-check-grace-period-seconds 30 \
  --deployment-configuration "minimumHealthyPercent=100,maximumPercent=200,deploymentCircuitBreaker={enable=true,rollback=true}" \
  --enable-ecs-managed-tags \
  --propagate-tags SERVICE
```

> 💡 **Insight quan trọng**:
> - **Deployment circuit breaker** tự động rollback nếu deployment fail → giảm downtime do bad deploy.
> - **Container Insights** bật từ đầu để có metrics chi tiết (CPU, memory, network per task).
> - `assignPublicIp=DISABLED` vì task ở private subnet và pull image hoàn toàn qua VPC endpoints (không cần Internet).

### 8.6. Auto Scaling

```bash
RESOURCE_ID="service/${PROJECT}-cluster/product-api"

aws application-autoscaling register-scalable-target \
  --service-namespace ecs \
  --scalable-dimension ecs:service:DesiredCount \
  --resource-id ${RESOURCE_ID} \
  --min-capacity 2 \
  --max-capacity 10

# Target tracking: giữ CPU ~ 60%
aws application-autoscaling put-scaling-policy \
  --policy-name cpu-target-60 \
  --service-namespace ecs \
  --scalable-dimension ecs:service:DesiredCount \
  --resource-id ${RESOURCE_ID} \
  --policy-type TargetTrackingScaling \
  --target-tracking-scaling-policy-configuration '{
    "TargetValue": 60.0,
    "PredefinedMetricSpecification": {"PredefinedMetricType": "ECSServiceAverageCPUUtilization"},
    "ScaleInCooldown": 60,
    "ScaleOutCooldown": 30
  }'
```

---

## 9. Module 7 — Test & Verify

### 9.1. Chờ service ổn định

```bash
aws ecs wait services-stable --cluster ${PROJECT}-cluster --services product-api

ALB_DNS=$(aws elbv2 describe-load-balancers \
  --names ${PROJECT}-alb \
  --query 'LoadBalancers[0].DNSName' --output text)
echo "API endpoint: http://${ALB_DNS}"
```

### 9.2. Test cache hit/miss

```bash
# Lần 1: cache MISS → đọc DDB, source="ddb"
curl -s http://${ALB_DNS}/products/electronics/E001 | jq

# Lần 2: cache HIT → source="cache" (nhanh hơn)
curl -s http://${ALB_DNS}/products/electronics/E001 | jq

# Update sản phẩm → invalidate cache
curl -s -X PUT http://${ALB_DNS}/products/electronics/E001 \
  -H 'Content-Type: application/json' \
  -d '{"category":"electronics","sku":"E001","name":"PM2.5 Sensor v2","price":1390000,"stock":50}' | jq

# Lần 3: cache đã invalidate → MISS lần nữa với data mới
curl -s http://${ALB_DNS}/products/electronics/E001 | jq
```

### 9.3. Load test đơn giản

```bash
# Cài hey nếu chưa có: brew install hey hoặc go install github.com/rakyll/hey@latest
hey -z 60s -c 50 http://${ALB_DNS}/products/electronics/E001

# Quan sát:
# - p50/p95/p99 latency
# - Cache hit ratio cao → DDB read units thấp
```

### 9.4. Verify metrics

Mở CloudWatch:
- **ECS Container Insights** → `ECS/ContainerInsights/CpuUtilized`, `MemoryUtilized`
- **ElastiCache** → `CacheHits`, `CacheMisses`, `EngineCPUUtilization`
- **DynamoDB** → `ConsumedReadCapacityUnits`, `SuccessfulRequestLatency`
- **ALB** → `TargetResponseTime`, `RequestCount`, `HTTPCode_Target_2XX_Count`

Cache hit ratio ≈ `CacheHits / (CacheHits + CacheMisses)` — kỳ vọng > 80% với workload điển hình.

---

## 10. Module 8 — Best Practice Summary Checklist

### 10.1. Security checklist

- [ ] Resource (ECS, Redis, DDB endpoint) đặt ở **private subnet**
- [ ] Security Group dùng **SG-to-SG reference**, không CIDR
- [ ] Redis bật **TLS in-transit** + **at-rest encryption** + **AUTH token**
- [ ] AUTH token & secrets lưu trong **Secrets Manager**, không hardcode
- [ ] DynamoDB **encryption at rest** + **PITR** + **deletion protection**
- [ ] **Task Role** tách riêng **Task Execution Role**, least-privilege
- [ ] ECR repo **immutable tag** + **image scan on push**
- [ ] Container **non-root user**
- [ ] **VPC Flow Logs** bật để forensic

### 10.2. Reliability checklist

- [ ] **Multi-AZ** cho Redis (replica + automatic failover)
- [ ] **2 ECS task tối thiểu**, ở 2 AZ
- [ ] **ALB health check** + **container health check**
- [ ] **Deployment circuit breaker** với rollback
- [ ] App **graceful degradation** khi Redis fail
- [ ] **Singleflight** chống cache stampede
- [ ] **Connection pooling** + **timeout** + **retry với backoff**

### 10.3. Performance checklist

- [ ] **VPC Gateway Endpoint** cho DynamoDB & S3 (0 phí, thấp latency)
- [ ] **VPC Interface Endpoints** cho ECR, Logs, Secrets — traffic không rời backbone AWS
- [ ] **DynamoDB on-demand** (hoặc auto-scaling provisioned)
- [ ] Cache **TTL hợp lý** (không quá ngắn → ít hit; không quá dài → stale)
- [ ] **Graviton (ARM64)** cho ECS task — ~20% rẻ hơn
- [ ] **Contributor Insights** để phát hiện hot key
- [ ] **DAX** nếu cần micro-second cho DDB read (cân nhắc thay/bổ sung Redis)

### 10.4. Cost checklist

- [ ] Sử dụng **Fargate Spot** cho non-critical workload (60-70% rẻ hơn)
- [ ] **ECR lifecycle policy** xóa old image
- [ ] **CloudWatch log retention** giới hạn (vd 14-30 ngày)
- [ ] **Reserved nodes** ElastiCache khi load ổn định (1y/3y)
- [ ] **DynamoDB on-demand → provisioned** khi pattern ổn định
- [ ] **Đánh giá NAT vs VPC Endpoints**: nếu app chỉ gọi AWS services → endpoints rẻ + an toàn hơn. Nếu gọi nhiều 3rd-party API → cân nhắc NAT.
- [ ] **Single-AZ endpoints** trong dev/staging để tiết kiệm 50% phí endpoint

### 10.5. Observability checklist

- [ ] **Container Insights** bật
- [ ] **CloudWatch Alarms**:
  - ECS: CPU > 80%, RunningTaskCount < desired
  - Redis: EngineCPU > 75%, Evictions > 0, ReplicationLag > 5s
  - DDB: ThrottledRequests > 0, SystemErrors > 0
  - ALB: 5XX rate, UnHealthyHostCount > 0
- [ ] **X-Ray tracing** trong app (có thể thêm với boto3 instrumentation)
- [ ] **Structured JSON logs** thay vì plaintext
- [ ] **Synthetic canary** với CloudWatch Synthetics (option)

---

## 11. Module 9 — Cleanup (rất quan trọng!)

Xóa theo **đúng thứ tự ngược** để tránh dependency error:

```bash
# 1. Scale ECS service về 0, rồi xóa
aws ecs update-service --cluster ${PROJECT}-cluster --service product-api --desired-count 0
aws ecs wait services-stable --cluster ${PROJECT}-cluster --services product-api
aws ecs delete-service --cluster ${PROJECT}-cluster --service product-api --force

# 2. Xóa cluster
aws ecs delete-cluster --cluster ${PROJECT}-cluster

# 3. Xóa ALB & target group
aws elbv2 delete-load-balancer --load-balancer-arn ${ALB_ARN}
sleep 30
aws elbv2 delete-target-group --target-group-arn ${TG_ARN}

# 4. Xóa ElastiCache (mất ~5 phút)
aws elasticache delete-replication-group --replication-group-id ${PROJECT}-redis
aws elasticache wait replication-group-deleted --replication-group-id ${PROJECT}-redis
aws elasticache delete-cache-subnet-group --cache-subnet-group-name ${PROJECT}-redis-subnet

# 5. Xóa DynamoDB (phải tắt deletion protection trước)
aws dynamodb update-table --table-name ${PROJECT}-products --no-deletion-protection-enabled
aws dynamodb delete-table --table-name ${PROJECT}-products

# 6. Xóa secrets (force để bỏ qua recovery window)
aws secretsmanager delete-secret --secret-id ${PROJECT}/redis/auth-token --force-delete-without-recovery

# 7. Xóa ECR repo
aws ecr delete-repository --repository-name ${PROJECT}/product-api --force

# 8. Xóa CloudFormation stacks (theo thứ tự ngược)
aws cloudformation delete-stack --stack-name ${PROJECT}-iam
aws cloudformation delete-stack --stack-name ${PROJECT}-sg
aws cloudformation wait stack-delete-complete --stack-name ${PROJECT}-sg
aws cloudformation delete-stack --stack-name ${PROJECT}-network

# 9. Xóa CloudWatch log group
aws logs delete-log-group --log-group-name /ecs/${PROJECT}/product-api
```

---

## 12. Troubleshooting

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
|---|---|---|
| Task fail "Unable to pull image" — stuck ở `Pulling fs layer` | Thiếu **S3 Gateway Endpoint** (ECR layers lưu ở S3) | Verify S3 endpoint có ở route table của private subnet |
| Task fail "Unable to pull image" — fail ở `GetAuthorizationToken` | Thiếu `ecr.api` interface endpoint, hoặc SG endpoint không cho 443 | Kiểm tra endpoint state `available` + SG inbound rule |
| Task fail "ResourceInitializationError: ... timeout" | DNS không resolve được vì `enableDnsHostnames`/`enableDnsSupport` = false, hoặc `PrivateDnsEnabled` của interface endpoint = false | Bật cả 3 setting |
| Task ở 1 AZ healthy, AZ kia fail | Interface endpoint không có ENI ở AZ đó | Add subnet AZ thiếu vào endpoint `SubnetIds` |
| Task fail "ResourceInitializationError: secrets" | Task Execution Role thiếu quyền `secretsmanager:GetSecretValue` | Cập nhật policy |
| App connect Redis timeout | Security Group không cho phép, hoặc app dùng `ssl=False` trong khi Redis bật TLS | Kiểm tra SG + cấu hình SSL client |
| Cache HIT nhưng latency vẫn cao | Cold connection pool, hoặc DNS lookup mỗi request | Reuse client (singleton); preconnect ở startup |
| ECS service không healthy | Health check path sai, hoặc port không match | Curl trực tiếp vào task private IP từ EC2 trong VPC để debug |
| DDB throttling | Hot key, hoặc capacity không đủ | Bật Contributor Insights, redesign PK |
| Cost cao bất thường | Interface endpoints chạy nhiều AZ không cần thiết, hoặc data transfer giữa AZ | Cân nhắc giảm AZ trong dev; xem CloudWatch metric `BytesProcessed` của endpoint |

---

## 13. Mở rộng (Optional)

Sau khi hoàn thành lab cơ bản, có thể thử nâng cao:

1. **Write-through cache**: cập nhật Redis ngay khi PUT thay vì invalidate.
2. **DynamoDB Streams + Lambda**: tự động invalidate cache khi data thay đổi từ kênh khác.
3. **DAX vs Redis benchmark**: triển khai song song, đo p99 và cost.
4. **AWS WAF** trước ALB: rate limit, SQL injection, bot protection.
5. **CloudFront** trước ALB: cache HTTP response ở edge.
6. **Blue/Green deployment** với CodeDeploy.
7. **Cross-region replication** với DynamoDB Global Tables.
8. **Tích hợp với PM2.5 use case**: dùng Redis cache cho sensor metadata + 1h sliding window aggregation, DynamoDB lưu raw readings với TTL 7 ngày.

---

## 14. Tài liệu tham khảo

- AWS Well-Architected Framework — Reliability & Security pillars
- DynamoDB Best Practices: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/best-practices.html
- ElastiCache Best Practices: https://docs.aws.amazon.com/AmazonElastiCache/latest/red-ug/BestPractices.html
- ECS Fargate Best Practices: https://docs.aws.amazon.com/AmazonECS/latest/bestpracticesguide/
- "Cache-aside" pattern — Microsoft Cloud Design Patterns
- Rick Houlihan — DynamoDB single-table design (re:Invent talks)

---

**Tác giả lab**: Built for Đà Nẵng AWS practitioners.
**Phiên bản**: 1.0 — May 2026
