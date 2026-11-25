/**
 * RatePath (ريت باث) — HTML5 Canvas Watermarked PNG Quote-Card Generator (1200×630)
 */

import { TRANSLATIONS } from "./i18n.js";

let _cachedLogoImage = null;
function loadBrandLogoImage() {
  if (_cachedLogoImage && _cachedLogoImage.complete && _cachedLogoImage.naturalWidth > 0) {
    return _cachedLogoImage;
  }
  const img = new Image();
  img.crossOrigin = "anonymous";
  img.src = "./icons/logo-mark.png";
  _cachedLogoImage = img;
  return img;
}

// Pre-warm logo image immediately so Canvas quote cards draw it synchronously
if (typeof window !== "undefined") {
  loadBrandLogoImage();
}

function drawRoundedRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/**
 * Render an institutional 1200x630 watermarked RatePath PNG onto `canvas`.
 */
export function renderQuoteCardCanvas(canvas, state) {
  if (!canvas || !state?.conversionResult || !state?.feed) return;

  const width = 1200;
  const height = 630;
  canvas.width = width;
  canvas.height = height;

  const ctx = canvas.getContext("2d");
  const lang = state.lang || "en";
  const t = TRANSLATIONS[lang] || TRANSLATIONS.en;
  const { sourceAsset, conversions, usdEquivalentFormatted } = state.conversionResult;

  // 1. Deep Obsidian Gradient Background
  const bgGrad = ctx.createLinearGradient(0, 0, width, height);
  bgGrad.addColorStop(0, "#070B14");
  bgGrad.addColorStop(0.55, "#0F172A");
  bgGrad.addColorStop(1, "#090D18");
  ctx.fillStyle = bgGrad;
  ctx.fillRect(0, 0, width, height);

  // 2. Subtle Institutional Terminal Grid
  ctx.strokeStyle = "rgba(148, 163, 184, 0.06)";
  ctx.lineWidth = 1;
  for (let gx = 0; gx < width; gx += 48) {
    ctx.beginPath();
    ctx.moveTo(gx, 0);
    ctx.lineTo(gx, height);
    ctx.stroke();
  }
  for (let gy = 0; gy < height; gy += 48) {
    ctx.beginPath();
    ctx.moveTo(0, gy);
    ctx.lineTo(width, gy);
    ctx.stroke();
  }

  // 3. Diagonal Anti-Tamper Brand Watermark Pattern
  ctx.save();
  ctx.translate(width / 2, height / 2);
  ctx.rotate(-Math.PI / 11);
  ctx.font = "700 34px system-ui, -apple-system, sans-serif";
  ctx.fillStyle = "rgba(212, 175, 55, 0.045)";
  ctx.textAlign = "center";
  for (let wy = -420; wy <= 420; wy += 140) {
    for (let wx = -700; wx <= 700; wx += 460) {
      ctx.fillText("RATEPATH • ريت باث", wx, wy);
    }
  }
  ctx.restore();

  // 4. Outer Bullion Gold Frame
  const goldGrad = ctx.createLinearGradient(0, 0, width, height);
  goldGrad.addColorStop(0, "#FDE68A");
  goldGrad.addColorStop(0.5, "#D4AF37");
  goldGrad.addColorStop(1, "#9A7B1C");
  ctx.strokeStyle = goldGrad;
  ctx.lineWidth = 3;
  drawRoundedRect(ctx, 22, 22, width - 44, height - 44, 24);
  ctx.stroke();

  // 5. Header Brand Emblem + Title & Timestamp
  const logoImg = loadBrandLogoImage();
  let brandTextX = 56;
  if (logoImg && logoImg.complete && logoImg.naturalWidth > 0) {
    ctx.save();
    drawRoundedRect(ctx, 52, 46, 68, 68, 16);
    ctx.clip();
    ctx.drawImage(logoImg, 52, 46, 68, 68);
    ctx.restore();
    ctx.strokeStyle = "rgba(212, 175, 55, 0.55)";
    ctx.lineWidth = 1.5;
    drawRoundedRect(ctx, 52, 46, 68, 68, 16);
    ctx.stroke();
    brandTextX = 136;
  } else if (logoImg && !logoImg.complete) {
    logoImg.onload = () => renderQuoteCardCanvas(canvas, state);
  }

  ctx.fillStyle = "#D4AF37";
  ctx.font = "800 34px system-ui, -apple-system, sans-serif";
  ctx.textAlign = "left";
  ctx.fillText("RatePath  |  ريت باث", brandTextX, 82);

  ctx.fillStyle = "#94A3B8";
  ctx.font = "500 17px system-ui, -apple-system, sans-serif";
  ctx.fillText("BANKING-GRADE DECIMAL CROSS-ASSET SNAPSHOT", brandTextX, 112);

  // Verified Pill on Top Right
  drawRoundedRect(ctx, width - 346, 52, 290, 44, 22);
  ctx.fillStyle = "rgba(16, 185, 129, 0.14)";
  ctx.fill();
  ctx.strokeStyle = "rgba(16, 185, 129, 0.5)";
  ctx.lineWidth = 1.5;
  ctx.stroke();

  ctx.fillStyle = "#34D399";
  ctx.font = "700 15px monospace";
  ctx.textAlign = "center";
  ctx.fillText(`UTC ${state.feed.meta.generated_at}`, width - 201, 80);

  // 6. Hero Active Conversion Banner
  drawRoundedRect(ctx, 56, 140, width - 112, 118, 18);
  ctx.fillStyle = "rgba(15, 23, 42, 0.88)";
  ctx.fill();
  ctx.strokeStyle = "rgba(212, 175, 55, 0.38)";
  ctx.lineWidth = 1.5;
  ctx.stroke();

  const srcAmountFormatted = conversions[sourceAsset.id]?.formatted || state.amount || "1";
  ctx.textAlign = "left";
  ctx.fillStyle = "#94A3B8";
  ctx.font = "600 16px system-ui, sans-serif";
  ctx.fillText(
    `${t.baseAssetLabel.toUpperCase()} (${t.assets[sourceAsset.id] || sourceAsset.code})`,
    84,
    176
  );

  ctx.fillStyle = "#F8FAFC";
  ctx.font = "800 44px monospace";
  ctx.fillText(`${srcAmountFormatted} ${sourceAsset.code}`, 84, 230);

  ctx.textAlign = "right";
  ctx.fillStyle = "#FDE68A";
  ctx.font = "700 24px monospace";
  ctx.fillText(`≈ $${usdEquivalentFormatted} USD`, width - 84, 218);

  // 7. 8-Card Institutional Benchmark Matrix (2 rows x 4 cols)
  const showcaseIds = [
    "XAU_OZ",
    "GRAM_24K",
    "GRAM_21K",
    "BTC",
    "EUR",
    "SAR",
    "AED",
    "EGP",
  ];
  const cardW = 258;
  const cardH = 122;
  const gapX = 18;
  const gapY = 18;
  const startX = 56;
  const startY = 282;

  showcaseIds.forEach((id, idx) => {
    const col = idx % 4;
    const row = Math.floor(idx / 4);
    const x = startX + col * (cardW + gapX);
    const y = startY + row * (cardH + gapY);

    drawRoundedRect(ctx, x, y, cardW, cardH, 14);
    ctx.fillStyle = "rgba(30, 41, 59, 0.68)";
    ctx.fill();
    ctx.strokeStyle =
      id === sourceAsset.id ? "rgba(212, 175, 55, 0.8)" : "rgba(148, 163, 184, 0.18)";
    ctx.lineWidth = 1.2;
    ctx.stroke();

    const assetMeta = state.catalog.byId[id];
    const conv = conversions[id];

    ctx.textAlign = "left";
    ctx.fillStyle = "#D4AF37";
    ctx.font = "700 16px monospace";
    ctx.fillText(assetMeta?.code || id, x + 18, y + 32);

    ctx.textAlign = "right";
    ctx.fillStyle = "#94A3B8";
    ctx.font = "500 13px system-ui, sans-serif";
    ctx.fillText(t.assets[id] || id, x + cardW - 18, y + 32);

    ctx.textAlign = "left";
    ctx.fillStyle = "#F8FAFC";
    ctx.font = "700 23px monospace";
    const valStr = conv?.formatted || "0.00";
    ctx.fillText(valStr.length > 15 ? valStr.slice(0, 15) : valStr, x + 18, y + 74);

    ctx.fillStyle = "#64748B";
    ctx.font = "500 13px monospace";
    ctx.fillText(`Spot: ${assetMeta?.spotDisplay || "-"}`, x + 18, y + 102);
  });

  // 8. Watermarked Institutional Footer
  ctx.textAlign = "left";
  ctx.fillStyle = "#94A3B8";
  ctx.font = "600 15px system-ui, sans-serif";
  ctx.fillText(
    "Verified Sources: Binance Vision • Bybit v5 • Swissquote XAU • Open FX  |  Zero-Float Decimal Engine",
    56,
    584
  );

  ctx.textAlign = "right";
  ctx.fillStyle = "#D4AF37";
  ctx.font = "800 17px system-ui, sans-serif";
  ctx.fillText("ratepath.io • ريت باث", width - 56, 584);
}

/**
 * Trigger instant PNG download from the rendered `<canvas>`.
 */
export function downloadCanvasAsPng(canvas, sourceCode = "USD") {
  if (!canvas) return;
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  const link = document.createElement("a");
  link.download = `RatePath-Quote-${sourceCode}-${stamp}.png`;
  link.href = canvas.toDataURL("image/png");
  document.body.appendChild(link);
  link.click();
  link.remove();
}

/**
 * Share PNG via Web Share API (mobile / Telegram) or copy to clipboard with download fallback.
 */
export async function shareCanvasPng(canvas, sourceCode = "USD") {
  if (!canvas) return "unavailable";
  return new Promise((resolve) => {
    canvas.toBlob(async (blob) => {
      if (!blob) {
        downloadCanvasAsPng(canvas, sourceCode);
        resolve("downloaded");
        return;
      }
      const file = new File([blob], `RatePath-${sourceCode}.png`, { type: "image/png" });
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        try {
          await navigator.share({
            title: "RatePath | ريت باث — Live Cross-Asset Snapshot",
            text: "Verified institutional cross-asset rates via RatePath (ريت باث)",
            files: [file],
          });
          resolve("shared");
          return;
        } catch {
          // User dismissed or share failed; fall through
        }
      }
      if (navigator.clipboard && window.ClipboardItem) {
        try {
          await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
          resolve("copied");
          return;
        } catch {
          // Fall through to direct download
        }
      }
      downloadCanvasAsPng(canvas, sourceCode);
      resolve("downloaded");
    }, "image/png");
  });
}
