// ui/story/arena.js: placeholder until the page is built (lead, 14 Oct 2026). Same contract as markets.js:
// export async function mount(root, ctx) -> { dispose() }. Data: ui/data/arena/index.json, written by scripts/export_arena.py.
export async function mount(root) {
  root.innerHTML = `<div class="st-placeholder"><div class="st-eyebrow">ARENA</div><div class="st-ph-title">The arena page is being built</div>
    <p>Its data file is written by <code>scripts/export_arena.py</code>.</p></div>`;
  return { dispose() {} };
}
