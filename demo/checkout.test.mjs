import { chromium } from 'playwright';
import { mutated } from 'shotbox';

const browser = await chromium.launch();
const context = await browser.newContext();
await context.route('http://shop.test/', (r) => r.fulfill({ contentType: 'text/html',
    body: '<button onclick="paid.hidden = false">Pay $42.50</button> <p id=paid hidden>Paid</p>' }));
await mutated(context);
const page = await context.newPage();
try
{
    await page.goto('http://shop.test/');
    await page.mouse.click(30, 18);                    // where Pay is drawn
    await page.getByText('Paid').waitFor({ timeout: 1000 });
}
finally
{
    await browser.close();
}
