import ts from "typescript";
import { readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";

const [resultFile, output] = process.argv.slice(2);
if (!resultFile || !output) throw new Error("Usage: knowledge-control-inventory.mjs result.json output.json");
const result = JSON.parse(await readFile(resultFile, "utf8"));
const actions = [];
if (result.passed) {
  for (const file of ["scripts/knowledge-e2e.mjs", ...(result.enhanced?.length ? ["scripts/knowledge-enhanced-e2e.mjs"] : [])]) {
    const code = await readFile(file, "utf8");
    for (const match of code.matchAll(/getBy(?:Role\("(?:button|checkbox|tab|link)",\s*\{\s*name:\s*"([^"]+)"[^}]*\}\)|Label\("([^"]+)"[^)]*\))\.(?:click|check|fill|selectOption|setInputFiles)\(/g)) {
      actions.push({ label: match[1] || match[2], file });
    }
  }
}
const rows = [];
for (const filename of (await readdir("src/components")).filter(name => /^Knowledge.*\.tsx$/.test(name) && !name.includes(".test."))) {
  const text = await readFile(path.join("src/components", filename), "utf8");
  const source = ts.createSourceFile(filename, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let ordinal = 0;
  function visit(node) {
    const element = ts.isJsxElement(node) ? node.openingElement : ts.isJsxSelfClosingElement(node) ? node : null;
    if (element && ["button", "input", "select", "textarea", "summary", "a"].includes(element.tagName.getText(source))) {
      let context = node;
      for (let parent = node.parent; parent && !ts.isSourceFile(parent); parent = parent.parent) {
        if (ts.isJsxElement(parent) && parent.openingElement.tagName.getText(source) === "label") { context = parent; break; }
        if (ts.isJsxElement(parent) && parent.openingElement.tagName.getText(source) === "section") break;
      }
      const code = context.getText(source).replace(/\s+/g, " ");
      const matched = actions.filter(action => action.label.length >= 3 && code.includes(action.label));
      rows.push({ id: "KBUI-" + filename.replace(".tsx", "") + "-" + (++ordinal), file: "src/components/" + filename,
        line: source.getLineAndCharacterOfPosition(element.getStart(source)).line + 1, tag: element.tagName.getText(source),
        control: code.slice(0, 450), result: matched.length ? "PASS" : "STATIC_ONLY",
        action_labels: [...new Set(matched.map(action => action.label))], evidence: matched.length ? resultFile : "No matching completed UI action; API evidence reported separately." });
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
}
const summary = { scope: "Source declarations matched to literal actions in a completed sequential E2E. Does not prove all dynamic roles/states or every repeated list row.",
  total: rows.length, activated: rows.filter(row => row.result === "PASS").length, rows };
await writeFile(output, JSON.stringify(summary, null, 2));
console.log(JSON.stringify({ total: summary.total, activated: summary.activated, output }));
