import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

export type Tweet = {
  n: number;
  text: string;
  chars: number;
};

export type Email = {
  id: string;
  audience: string;
  subject: string;
  body: string;
};

export type ChannelPost = {
  id: string;
  channel: string;
  file: string;
  title?: string;
  body: string;
  tweets?: Tweet[];
  emails?: Email[];
  claimIds: string;
};

function copyPath(filename: string): string {
  const candidates = [
    resolve(process.cwd(), "../launch/copy", filename),
    resolve(process.cwd(), "launch/copy", filename),
  ];
  const hit = candidates.find((path) => existsSync(path));
  if (!hit) {
    throw new Error(`missing launch copy: ${filename}`);
  }
  return hit;
}

function stripMeta(raw: string): { docTitle: string; text: string; claimIds: string } {
  let text = raw.replace(/\r\n/g, "\n").trim();
  const claim = text.match(/\nclaim_ids:\s*(.+)\s*$/);
  const claimIds = claim?.[1]?.trim() ?? "";
  text = text.replace(/\nclaim_ids:[\s\S]*$/, "").trim();
  const titleMatch = text.match(/^#\s+(.+)\n/);
  const docTitle = titleMatch?.[1] ?? "";
  text = text.replace(/^#\s+.+\n+/, "");
  return { docTitle, text, claimIds };
}

function load(filename: string) {
  return stripMeta(readFileSync(copyPath(filename), "utf8"));
}

function splitSections(text: string, heading: RegExp): { label: string; extra: string; body: string }[] {
  const matches = [...text.matchAll(heading)];
  return matches.map((match, index) => {
    const start = match.index! + match[0].length;
    const end = index + 1 < matches.length ? matches[index + 1].index! : text.length;
    return {
      label: match[1] ?? "",
      extra: match[2] ?? "",
      body: text.slice(start, end).trim(),
    };
  });
}

export function loadPosts(): ChannelPost[] {
  const x = load("x-launch-thread.md");
  const tweets = splitSections(x.text, /^##\s+(\d+)\s*$/gm).map((section) => ({
    n: Number(section.label),
    text: section.body,
    chars: section.body.length,
  }));
  const xBody = x.text.replace(/^Reviewer:.*\n+/, "").trim();

  const hn = load("hacker-news.md");
  const hnTitle = hn.text.match(/^Title:\s*(.+)$/m)?.[1];
  const hnBody = hn.text.replace(/^Title:\s*.+\n+/, "").trim();

  const li = load("linkedin.md");
  const zh = load("chinese-long-post.md");
  const zhTitle = zh.text.match(/^标题：\s*(.+)$/m)?.[1];
  const zhBody = zh.text.replace(/^标题：\s*.+\n+/, "").trim();

  const hf = load("huggingface-summary.md");
  const mail = load("researcher-outreach.md");
  const emails = splitSections(mail.text, /^##\s+([A-Z])\.\s+(.+)$/gm).map((section) => {
    const subject = section.body.match(/^Subject:\s*(.+)$/m)?.[1]?.trim() ?? "";
    const body = section.body.replace(/^Subject:\s*.+\n+/, "").trim();
    return { id: section.label, audience: section.extra.trim(), subject, body };
  });

  return [
    { id: "x", channel: "X thread", file: "launch/copy/x-launch-thread.md", body: xBody, tweets, claimIds: x.claimIds },
    { id: "hn", channel: "Hacker News", file: "launch/copy/hacker-news.md", title: hnTitle, body: hnBody, claimIds: hn.claimIds },
    { id: "li", channel: "LinkedIn", file: "launch/copy/linkedin.md", body: li.text.trim(), claimIds: li.claimIds },
    { id: "zh", channel: "中文长文", file: "launch/copy/chinese-long-post.md", title: zhTitle, body: zhBody, claimIds: zh.claimIds },
    { id: "hf", channel: "Hugging Face", file: "launch/copy/huggingface-summary.md", body: hf.text.trim(), claimIds: hf.claimIds },
    { id: "mail", channel: "Researcher outreach", file: "launch/copy/researcher-outreach.md", body: mail.text.trim(), emails, claimIds: mail.claimIds },
  ];
}

export function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function renderInline(value: string): string {
  return escapeHtml(value)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" rel="noreferrer">$1</a>');
}

export function renderBlocks(value: string): string {
  const chunks = value.replace(/\r\n/g, "\n").split(/\n```/);
  const html: string[] = [];
  for (let i = 0; i < chunks.length; i += 1) {
    const chunk = chunks[i];
    if (i % 2 === 1) {
      const newline = chunk.indexOf("\n");
      const code = newline === -1 ? chunk : chunk.slice(newline + 1);
      html.push(`<pre class="preview-code">${escapeHtml(code.replace(/\n$/, ""))}</pre>`);
      continue;
    }
    for (const block of chunk.split(/\n\n+/)) {
      const trimmed = block.trim();
      if (!trimmed) continue;
      if (trimmed.startsWith("## ")) {
        html.push(`<h3>${renderInline(trimmed.slice(3))}</h3>`);
        continue;
      }
      if (trimmed.startsWith("- ")) {
        const items = trimmed
          .split("\n")
          .filter((line) => line.startsWith("- "))
          .map((line) => `<li>${renderInline(line.slice(2))}</li>`)
          .join("");
        html.push(`<ul>${items}</ul>`);
        continue;
      }
      html.push(`<p>${renderInline(trimmed).replace(/\n/g, "<br />")}</p>`);
    }
  }
  return html.join("");
}
