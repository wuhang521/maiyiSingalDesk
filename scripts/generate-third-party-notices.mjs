import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";

const root = process.cwd();
const lock = JSON.parse(await readFile(path.join(root, "package-lock.json"), "utf8"));
const outputPath = path.join(root, "release", "THIRD_PARTY_NOTICES.txt");
const packages = [];

for (const [packagePath, metadata] of Object.entries(lock.packages || {})) {
  if (!packagePath.startsWith("node_modules/") || metadata.dev === true) continue;

  const directory = path.join(root, packagePath);
  const manifest = JSON.parse(await readFile(path.join(directory, "package.json"), "utf8"));
  const entries = await readdir(directory);
  const licenseFile = entries.find((name) => /^(licen[cs]e|copying)(\..*)?$/i.test(name));
  const licenseText = licenseFile
    ? await readFile(path.join(directory, licenseFile), "utf8")
    : `License identifier: ${manifest.license || metadata.license || "Unknown"}`;

  packages.push({
    name: manifest.name || packagePath.replace(/^node_modules\//, ""),
    version: manifest.version || metadata.version || "unknown",
    licenseText: licenseText.trim(),
  });
}

packages.sort((left, right) => left.name.localeCompare(right.name));

const sections = packages.map(
  ({ name, version, licenseText }) =>
    `================================================================================\n${name} ${version}\n================================================================================\n${licenseText}`,
);
const notice = [
  "迈医协同 third-party notices",
  "",
  "迈医协同 includes third-party software. The following notices are provided by their respective copyright holders.",
  "",
  ...sections,
  "",
].join("\n");

await mkdir(path.dirname(outputPath), { recursive: true });
await writeFile(outputPath, notice, "utf8");
console.log(`Generated ${outputPath} for ${packages.length} packages.`);
