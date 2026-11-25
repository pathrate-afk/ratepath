/**
 * RatePath (ريت باث) — Official Hardcoded Syrian Revolution Flag SVG & Canvas Renderer
 *
 * Strictly enforced geometry (top-to-bottom, zero external CDN dependencies):
 * - Top band: Green (#007A3D)
 * - Middle band: White (#FFFFFF) with 3 horizontally centered five-pointed Red stars (#CE1126)
 * - Bottom band: Black (#000000)
 */

export const SYRIA_FLAG_COLORS = Object.freeze({
  TOP_GREEN: "#007A3D",
  MIDDLE_WHITE: "#FFFFFF",
  STARS_RED: "#CE1126",
  BOTTOM_BLACK: "#000000",
});

/**
 * Hardcoded inline SVG of the Syrian Revolution Flag (Green-White-Black + 3 Red 5-pointed stars).
 */
export function getSyriaRevolutionFlagSvg(className = "w-6 h-4 rounded-[3px] shrink-0 shadow-sm ring-1 ring-white/15") {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 600" class="${className}" role="img" aria-label="Syrian Revolution Flag (SYP)" data-flag="syria-revolution">
  <rect x="0" y="0" width="900" height="200" fill="#007A3D"/>
  <rect x="0" y="200" width="900" height="200" fill="#FFFFFF"/>
  <rect x="0" y="400" width="900" height="200" fill="#000000"/>
  <polygon fill="#CE1126" points="225,236 239.3,280.3 285.9,280.2 248.2,307.5 262.6,351.8 225,324.4 187.4,351.8 201.8,307.5 164.1,280.2 210.7,280.3"/>
  <polygon fill="#CE1126" points="450,236 464.3,280.3 510.9,280.2 473.2,307.5 487.6,351.8 450,324.4 412.4,351.8 426.8,307.5 389.1,280.2 435.7,280.3"/>
  <polygon fill="#CE1126" points="675,236 689.3,280.3 735.9,280.2 698.2,307.5 712.6,351.8 675,324.4 637.4,351.8 651.8,307.5 614.1,280.2 660.7,280.3"/>
</svg>`;
}

function drawFivePointedStar(ctx, cx, cy, outerR, innerR, fillStyle) {
  ctx.beginPath();
  for (let i = 0; i < 10; i++) {
    const r = i % 2 === 0 ? outerR : innerR;
    const angle = (-Math.PI / 2) + (i * Math.PI) / 5;
    const px = cx + Math.cos(angle) * r;
    const py = cy + Math.sin(angle) * r;
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
  ctx.closePath();
  ctx.fillStyle = fillStyle;
  ctx.fill();
}

/**
 * Draw the exact Syrian Revolution Flag directly onto an HTML5 `<canvas>` context.
 */
export function drawSyriaRevolutionFlagOnCanvas(ctx, x, y, width, height) {
  const bandH = height / 3;
  ctx.save();

  // Top band: Green (#007A3D)
  ctx.fillStyle = SYRIA_FLAG_COLORS.TOP_GREEN;
  ctx.fillRect(x, y, width, bandH);

  // Middle band: White (#FFFFFF)
  ctx.fillStyle = SYRIA_FLAG_COLORS.MIDDLE_WHITE;
  ctx.fillRect(x, y + bandH, width, bandH);

  // Bottom band: Black (#000000)
  ctx.fillStyle = SYRIA_FLAG_COLORS.BOTTOM_BLACK;
  ctx.fillRect(x, y + bandH * 2, width, bandH);

  // 3 horizontally centered five-pointed Red stars (#CE1126) in the middle white band
  const starY = y + height / 2;
  const outerR = bandH * 0.34;
  const innerR = outerR * 0.382;
  for (const frac of [0.25, 0.5, 0.75]) {
    drawFivePointedStar(ctx, x + width * frac, starY, outerR, innerR, SYRIA_FLAG_COLORS.STARS_RED);
  }

  // Crisp subtle hairline border
  ctx.strokeStyle = "rgba(255, 255, 255, 0.22)";
  ctx.lineWidth = 1;
  ctx.strokeRect(x, y, width, height);
  ctx.restore();
}
