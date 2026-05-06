import { test, expect } from '@playwright/test';

test('should display product details from backend', async ({ page }) => {
  // Go to the home page
  await page.goto('/');

  // Check for the header
  await expect(page.locator('h1')).toContainText('Product Detail');

  // Wait for the product name to appear (handles loading state)
  const productName = page.locator('#product-name');
  await expect(productName).toBeVisible({ timeout: 15000 });
  await expect(productName).toContainText('iPhone 15 Pro');

  // Check price and stock
  await expect(page.locator('text=$999.99')).toBeVisible();
  await expect(page.locator('text=50 units')).toBeVisible();

  // Check source badge (it could be DYNAMODB or CACHE)
  const sourceBadge = page.locator('#product-source');
  await expect(sourceBadge).toBeVisible();
  const sourceText = await sourceBadge.innerText();
  expect(['DYNAMODB', 'CACHE']).toContain(sourceText);
});
