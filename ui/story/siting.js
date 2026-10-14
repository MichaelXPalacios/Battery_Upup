// ui/story/siting.js: placeholder until the page is built (lead, 14 Oct 2026). Same contract as markets.js:
// export async function mount(root, ctx) -> { dispose() }. Data: ui/data/siting/index.json, written by scripts/export_siting.py.
export async function mount(root) {
  root.innerHTML = `<div class="st-placeholder"><div class="st-eyebrow">SITING</div><div class="st-ph-title">The siting page is being built</div>
    <p>Its data file is written by <code>scripts/export_siting.py</code>.</p></div>`;
  return { dispose() {} };
}
