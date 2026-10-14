// ui/story/dispatch.js: placeholder until the page is built (lead, 14 Oct 2026). Same contract as markets.js:
// export async function mount(root, ctx) -> { dispose() }. Data: ui/data/dispatch/index.json, written by scripts/export_dispatch.py.
export async function mount(root) {
  root.innerHTML = `<div class="st-placeholder"><div class="st-eyebrow">DISPATCH</div><div class="st-ph-title">The dispatch page is being built</div>
    <p>Its data file is written by <code>scripts/export_dispatch.py</code>.</p></div>`;
  return { dispose() {} };
}
