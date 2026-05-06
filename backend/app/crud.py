import aioboto3
from datetime import datetime
from .config import settings
from .schemas import UserCreate, UserUpdate

class DynamoDBClient:
    def __init__(self):
        self.session = aioboto3.Session()
        self.dynamo_resource = None
        self._exit_stack = None

    async def connect(self):
        self._exit_stack = aioboto3.utils.AsyncExitStack()
        self.dynamo_resource = await self._exit_stack.enter_async_context(
            self.session.resource("dynamodb", region_name=settings.AWS_REGION)
        )

    async def close(self):
        if self._exit_stack:
            await self._exit_stack.aclose()

    async def get_user(self, user_id: str):
        table = await self.dynamo_resource.Table(settings.DYNAMODB_TABLE_NAME)
        response = await table.get_item(Key={"user_id": user_id})
        return response.get("Item")

    async def create_user(self, user: UserCreate):
        now = datetime.utcnow().isoformat()
        item = user.model_dump()
        item["created_at"] = now
        item["updated_at"] = now
        
        table = await self.dynamo_resource.Table(settings.DYNAMODB_TABLE_NAME)
        await table.put_item(Item=item)
        return item

    async def update_user(self, user_id: str, user_update: UserUpdate):
        now = datetime.utcnow().isoformat()
        update_data = user_update.model_dump(exclude_unset=True)
        update_data["updated_at"] = now
        
        expression = "set " + ", ".join(f"#{k} = :{k}" for k in update_data.keys())
        names = {f"#{k}": k for k in update_data.keys()}
        values = {f":{k}": v for k, v in update_data.items()}

        table = await self.dynamo_resource.Table(settings.DYNAMODB_TABLE_NAME)
        await table.update_item(
            Key={"user_id": user_id},
            UpdateExpression=expression,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values
        )
        return await self.get_user(user_id)

    async def delete_user(self, user_id: str):
        table = await self.dynamo_resource.Table(settings.DYNAMODB_TABLE_NAME)
        await table.delete_item(Key={"user_id": user_id})

db = DynamoDBClient()
