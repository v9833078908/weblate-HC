import {chromium} from '@playwright/test';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
const browser = await chromium.launch({headless:true});
try {
  const page = await browser.newPage();
  for (const [label,width,height] of [['desktop',1280,720],['mobile',390,844]]) {
    await page.setViewportSize({width,height});
    await page.goto(process.env.WIZARD_LOGIN_PREVIEW_URL || pathToFileURL(resolve('../artifacts/login-preview.html')).href);
    const boxes = await Promise.all(['#username','#password','#submit'].map(s=>page.locator(s).boundingBox()));
    if (boxes.some(b=>Math.abs(b.x-boxes[0].x)>1 || Math.abs(b.width-boxes[0].width)>1)) throw Error('Controls not aligned');
    const overflow = await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
    if (overflow) throw Error('Horizontal overflow');
    await page.keyboard.press('Tab');
    if (!(await page.locator('#username').evaluate(el=>el===document.activeElement))) throw Error('Keyboard navigation');
    await page.screenshot({path:`../artifacts/login-${label}.png`,fullPage:true});
  }
  console.log('LOGIN_DESKTOP_MOBILE_ALIGNMENT_KEYBOARD_OK');
} finally {await browser.close();}
