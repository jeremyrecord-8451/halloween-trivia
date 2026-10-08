// Shared helpers for the host and player pages.
const ICONS = ["🎃", "👻", "🦇", "💀"];

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Something went wrong.");
  return data;
}

function $(id) { return document.getElementById(id); }

function show(id) {
  document.querySelectorAll(".screen").forEach((el) => el.classList.toggle("hidden", el.id !== id));
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Drifting background decorations.
const FLOAT_IMAGES = ["img/mummy.png", "img/pirate.png", "img/pumpkin-lady.png"];

(function decorate() {
  const EMOJI_COUNT = 10;
  const IMAGE_COUNT = 8;
  // Shuffle so the characters and emoji are spread out rather than clumped.
  const kinds = [
    ...Array.from({ length: EMOJI_COUNT }, (_, i) => ({ emoji: ICONS[i % ICONS.length] })),
    ...Array.from({ length: IMAGE_COUNT }, (_, i) => ({ image: FLOAT_IMAGES[i % FLOAT_IMAGES.length] })),
  ].sort(() => Math.random() - 0.5);
  for (const kind of kinds) {
    let f;
    if (kind.image) {
      f = el("img", "float");
      f.src = kind.image;
      f.alt = "";
    } else {
      f = el("div", "float", kind.emoji);
    }
    f.style.left = Math.random() * 95 + "vw";
    f.style.animationDuration = 14 + Math.random() * 16 + "s";
    f.style.animationDelay = -Math.random() * 30 + "s";
    document.body.appendChild(f);
  }
})();
