const path = require("path");
const sharp = require("sharp");

const root = path.resolve(__dirname, "..");
const symbolPath = path.join(root, "src", "assets", "mayi-symbol.png");
const appLogoPath = path.join(root, "src", "assets", "mayi-logo.png");
const buildIconPath = path.join(root, "build", "icon.png");
const label = Buffer.from(`
  <svg width="1024" height="1024" xmlns="http://www.w3.org/2000/svg">
    <text
      x="512"
      y="840"
      text-anchor="middle"
      fill="#168a6a"
      font-family="Microsoft YaHei, PingFang SC, Noto Sans CJK SC, sans-serif"
      font-size="116"
      font-weight="700"
      letter-spacing="10"
    >迈医协同</text>
  </svg>
`);

sharp(symbolPath)
  .resize(1024, 1024)
  .composite([{ input: label, top: 0, left: 0 }])
  .png()
  .toBuffer()
  .then((icon) => Promise.all([
    sharp(icon).toFile(appLogoPath),
    sharp(icon).toFile(buildIconPath),
  ]))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
