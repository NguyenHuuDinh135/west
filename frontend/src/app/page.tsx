'use client';

import { useEffect, useState } from 'react';

interface Product {
  source: string;
  category: string;
  sku: string;
  name: string;
  price: number;
  stock: number;
}

export default function Home() {
  const [product, setProduct] = useState<Product | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchProduct = async () => {
    setLoading(true);
    try {
      // Use the Next.js rewrite proxy to avoid CORS
      const res = await fetch(`/api/backend/products/gadgets/iphone-15`);
      if (!res.ok) {
        throw new Error('Product not found');
      }
      const data = await res.json();
      setProduct(data);
      setError(null);
    } catch (err: any) {
      setError(err.message);
      setProduct(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProduct();
  }, []);

  return (
    <main className="min-h-screen p-24 bg-white text-black">
      <div className="max-w-md mx-auto bg-gray-50 p-8 rounded-xl shadow-sm border border-gray-100">
        <h1 className="text-2xl font-bold mb-6">Product Detail</h1>
        
        {loading && <p className="text-gray-500">Loading product...</p>}
        
        {error && (
          <div className="bg-red-50 text-red-600 p-4 rounded-lg mb-4">
            Error: {error}
          </div>
        )}

        {product && (
          <div className="space-y-4">
            <div className="flex justify-between items-center">
              <span className="text-sm font-medium text-gray-500 uppercase tracking-wider">Name</span>
              <span id="product-name" className="text-lg font-semibold">{product.name}</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-sm font-medium text-gray-500 uppercase tracking-wider">Price</span>
              <span className="text-lg text-blue-600 font-bold">${product.price}</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-sm font-medium text-gray-500 uppercase tracking-wider">Stock</span>
              <span className="text-lg">{product.stock} units</span>
            </div>
            <div className="pt-4 border-t border-gray-200 mt-4">
              <div className="flex justify-between items-center text-xs">
                <span className="text-gray-400 italic">Source:</span>
                <span id="product-source" className={`px-2 py-1 rounded font-bold ${product.source === 'cache' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'}`}>
                  {product.source.toUpperCase()}
                </span>
              </div>
            </div>
            <button 
              onClick={fetchProduct}
              className="w-full mt-6 bg-black text-white py-3 rounded-lg font-medium hover:bg-gray-800 transition-colors"
            >
              Refresh Data
            </button>
          </div>
        )}
      </div>
    </main>
  );
}
