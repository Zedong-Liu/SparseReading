#!/usr/bin/env node
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DIST = resolve(ROOT, "dist");
const BASE = "/SparseReading/";

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...walk(path));
    else out.push(path);
  }
  return out;
}

function destFor(href) {
  const [pathPart] = href.split("#");
  if (!pathPart || pathPart.startsWith("mailto:") || pathPart.startsWith("javascript:")) {
    return { skip: true };
  }
  if (pathPart.startsWith("http://") || pathPart.startsWith("https://")) {
    return { external: pathPart };
  }
  const cleaned = pathPart.startsWith(BASE) ? pathPart.slice(BASE.length) : pathPart.replace(/^\//, "");
  if (!cleaned) return { file: join(DIST, "index.html") };
  const file = join(DIST, cleaned);
  const asIndex = join(DIST, cleaned, "index.html");
  if (existsSync(file) && statSync(file).isFile()) return { file };
  if (existsSync(asIndex)) return { file: asIndex };
  return { missing: file };
}

if (!existsSync(DIST)) {
  console.error("dist/ missing. Run npm run build first.");
  process.exit(1);
}

const htmlFiles = walk(DIST).filter((path) => path.endsWith(".html"));
const hrefs = new Set();
for (const file of htmlFiles) {
  const text = readFileSync(file, "utf8");
  for (const match of text.matchAll(/(?:href|src)="([^"]+)"/g)) {
    hrefs.add(match[1]);
  }
}

const required = [`${BASE}og.png`, `${BASE}favicon.svg`, `${BASE}llms.txt`, `${BASE}sitemap.xml`, `${BASE}robots.txt`];
for (const href of required) hrefs.add(href);

const errors = [];
const externals = [];
for (const href of hrefs) {
  const dest = destFor(href);
  if (dest.skip) continue;
  if (dest.external) {
    externals.push(dest.external);
    continue;
  }
  if (dest.missing) errors.push(`missing ${href} -> ${dest.missing}`);
}

const uniqueExt = [...new Set(externals)].filter(
  (url) => url.startsWith("https://arxiv.org") || url.startsWith("https://github.com"),
);

const heads = await Promise.all(
  uniqueExt.map(async (url) => {
    try {
      const res = await fetch(url, { method: "HEAD", redirect: "follow" });
      return { url, status: res.status };
    } catch (err) {
      return { url, status: `ERR ${err}` };
    }
  }),
);

for (const row of heads) {
  if (typeof row.status === "number" && row.status >= 400) {
    errors.push(`${row.status} ${row.url}`);
  }
  if (typeof row.status === "string") errors.push(`${row.status} ${row.url}`);
}

console.log(`checked ${hrefs.size} href/src values in ${htmlFiles.length} html files`);
for (const row of heads) console.log(`  ${row.status} ${row.url}`);
if (errors.length) {
  console.error("FAIL");
  for (const err of errors) console.error(" ", err);
  process.exit(1);
}
console.log("PASS");
