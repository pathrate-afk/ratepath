/**
 * RatePath (ريت باث) — Interactive Per-Asset Detail Modal Controller
 *
 * When a visitor taps ANY currency ($ USD, EUR, TRY, SYP, RUB...), Gold/Silver karat,
 * or Crypto coin on the homepage, opens a dedicated institutional drawer with:
 * 1. Detailed Multi-Timeframe Analysis (1D / 1W / 1M / 1Y) + Interactive SVG Chart + RSI/EMA
 * 2. Instant Multi-Currency Converter directly below (exact BigInt `decimal-math.js`!)
 * 3. Latest Asset-Specific News Headlines (AR/EN) at the bottom
 */

import { convertAllAssets } from "./decimal-math.js";
import { getSyriaRevolutionFlagSvg } from "./syria-flag.js";

function esc(v) {
  return String(v ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeUrl(u) {
  const s = String(u || "").trim();
  return /^https?:\/\//i.test(s) ? s : "https://ratepathlive.com/";
}

function renderSvgChart(series, isUp) {
  if (!Array.isArray(series) || series.length < 2) {
    return "";
  }
  const w = 640;
  const h = 150;
  const padX = 8;
  const padY = 14;
  const vals = series.map((p) => Number(p.v)).filter(Number.isFinite);
  if (vals.length < 2) return "";

  const minV = Math.min(...vals);
  const maxV = Math.max(...vals);
  const span = maxV - minV || maxV * 0.01 || 1;

  const coords = vals.map((v, idx) => {
    const x = padX + (idx / (vals.length - 1)) * (w - padX * 2);
    const y = h - padY - ((v - minV) / span) * (h - padY * 2);
    return [x.toFixed(1), y.toFixed(1)];
  });

  const linePath = coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x},${y}`).join(" ");
  const areaPath = `${linePath} L${coords[coords.length - 1][0]},${h - 2} L${coords[0][0]},${h - 2} Z`;
  const strokeColor = isUp ? "#34d399" : "#f43f5e";
  const fillStart = isUp ? "rgba(16,185,129,0.28)" : "rgba(244,63,94,0.26)";

  return `
    <svg viewBox="0 0 ${w} ${h}" class="w-full h-36 sm:h-40 block" preserveAspectRatio="none" role="img" aria-label="Price chart">
      <defs>
        <linearGradient id="rpAssetChartGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${fillStart}" />
          <stop offset="100%" stop-color="rgba(15,23,42,0)" />
        </linearGradient>
      </defs>
      <path d="${areaPath}" fill="url(#rpAssetChartGrad)" />
      <path d="${linePath}" fill="none" stroke="${strokeColor}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />
      <circle cx="${coords[coords.length - 1][0]}" cy="${coords[coords.length - 1][1]}" r="4" fill="${strokeColor}" />
    </svg>
  `;
}

export function createAssetDetailController({
  getState,
  getLocalizedAssetName,
  setMainBaseAsset,
  showToast,
}) {
  const detailState = {
    open: false,
    assetId: null,
    activeTf: "1d",
    loading: false,
    data: null,
    convAmount: "100",
    convTargetId: "USD",
    convSwapped: false,
    cache: new Map(),
  };

  function getAssetObj(assetId) {
    return getState()?.catalog?.byId?.[assetId] || null;
  }

  function defaultTargetFor(assetId) {
    if (assetId === "USD") return "EUR";
    return "USD";
  }

  async function fetchAssetDetailData(asset, lang) {
    const cacheKey = `${asset.id}:${lang}`;
    const cached = detailState.cache.get(cacheKey);
    if (cached && Date.now() - cached.ts < 180000) {
      detailState.data = cached.payload;
      detailState.loading = false;
      renderModalContent();
      return;
    }

    detailState.loading = true;
    detailState.data = null;
    renderModalContent();

    try {
      const nameParam = lang === "ar" ? asset.nameAr || asset.nameEn : asset.nameEn || asset.code;
      const resp = await fetch(
        `/api/asset-detail?id=${encodeURIComponent(asset.id)}&code=${encodeURIComponent(asset.code)}&category=${encodeURIComponent(asset.category)}&name=${encodeURIComponent(nameParam || asset.code)}&lang=${encodeURIComponent(lang)}`
      );
      if (resp.ok) {
        const payload = await resp.json();
        detailState.cache.set(cacheKey, { ts: Date.now(), payload });
        // Only apply if modal is still showing this asset
        if (detailState.assetId === asset.id) {
          detailState.data = payload;
        }
      }
    } catch {
      // Keep graceful error/fallback state
    } finally {
      if (detailState.assetId === asset.id) {
        detailState.loading = false;
        renderModalContent();
      }
    }
  }

  function openAssetDetail(assetId) {
    const asset = getAssetObj(assetId);
    if (!asset) return;
    const modal = document.getElementById("modal-asset-detail");
    if (!modal) return;

    detailState.open = true;
    detailState.assetId = asset.id;
    detailState.activeTf = "1d";
    detailState.convAmount = asset.category === "fiat" ? "100" : "1";
    detailState.convTargetId = defaultTargetFor(asset.id);
    detailState.convSwapped = false;

    modal.classList.remove("hidden");
    modal.classList.add("flex");
    document.body.style.overflow = "hidden";

    const amtInput = document.getElementById("detail-conv-amount");
    if (amtInput) amtInput.value = detailState.convAmount;

    populateDetailTargetSelect();
    renderModalContent();
    fetchAssetDetailData(asset, getState().lang);
  }

  function closeAssetDetail() {
    const modal = document.getElementById("modal-asset-detail");
    if (!modal) return;
    detailState.open = false;
    modal.classList.add("hidden");
    modal.classList.remove("flex");
    const otherOpen = ["modal-quote-card", "modal-vip-alerts", "modal-asset-picker", "modal-auth-sync", "modal-usdt-upgrade"].some(
      (id) => {
        const el = document.getElementById(id);
        return el && !el.classList.contains("hidden");
      }
    );
    if (!otherOpen) {
      document.body.style.overflow = "";
    }
  }

  function populateDetailTargetSelect() {
    const sel = document.getElementById("detail-conv-target");
    const appState = getState();
    if (!sel || !appState?.catalog) return;

    const allItems = appState.catalog.allSearchable || appState.catalog.ordered || [];
    const seen = new Set();
    const list = [];
    // Prioritize workspace assets first, then the rest of catalog
    for (const id of appState.workspace?.ordered_ids || []) {
      const a = appState.catalog.byId[id];
      if (a && !seen.has(a.id)) {
        seen.add(a.id);
        list.push(a);
      }
    }
    for (const a of allItems) {
      if (a && !seen.has(a.id)) {
        seen.add(a.id);
        list.push(a);
      }
    }

    sel.innerHTML = list
      .map((a) => {
        const name = getLocalizedAssetName(a);
        return `<option value="${esc(a.id)}" ${a.id === detailState.convTargetId ? "selected" : ""}>${esc(a.code)} — ${esc(name)}</option>`;
      })
      .join("");
    if (detailState.convTargetId && appState.catalog.byId[detailState.convTargetId]) {
      sel.value = detailState.convTargetId;
    }
  }

  function renderDetailConverter() {
    const resultEl = document.getElementById("detail-conv-result");
    const quickGrid = document.getElementById("detail-conv-quick-grid");
    const sourceCodeEl = document.getElementById("detail-conv-source-code");
    const appState = getState();
    const asset = getAssetObj(detailState.assetId);
    if (!asset || !appState?.catalog) return;

    const targetAsset = appState.catalog.byId[detailState.convTargetId] || appState.catalog.byId.USD;
    const effectiveFrom = detailState.convSwapped ? targetAsset : asset;
    const effectiveTo = detailState.convSwapped ? asset : targetAsset;

    if (sourceCodeEl) sourceCodeEl.textContent = effectiveFrom.code;

    const quickIds = ["USD", "EUR", "GBP", "SAR", "AED", "TRY", "EGP", "SYP", "GRAM_21K", "XAU_OZ", "BTC"]
      .filter((id) => id !== effectiveFrom.id && appState.catalog.byId[id])
      .slice(0, 6);
    const targetIds = [...new Set([effectiveTo.id, ...quickIds])];
    const conv = convertAllAssets(detailState.convAmount || "0", effectiveFrom.id, appState.catalog, targetIds);
    const targetVal = conv.conversions?.[effectiveTo.id]?.formatted || "0";

    if (resultEl) {
      resultEl.innerHTML = `
        <span class="text-xs text-slate-400 font-mono" dir="ltr">${esc(detailState.convAmount || "0")} ${esc(effectiveFrom.code)} =</span>
        <strong class="text-lg sm:text-xl font-extrabold font-mono text-amber-300 tabular-nums" dir="ltr">${esc(targetVal)} ${esc(effectiveTo.code)}</strong>
      `;
    }

    if (quickGrid) {
      quickGrid.innerHTML = quickIds
        .map((qid) => {
          const qa = appState.catalog.byId[qid];
          const val = conv.conversions?.[qid]?.formatted || "0";
          const name = getLocalizedAssetName(qa);
          return `
            <button type="button" data-detail-pick-target="${esc(qid)}"
              class="p-2 rounded-lg bg-slate-950/85 hover:bg-slate-800 border border-slate-800 hover:border-amber-400/40 text-start transition-colors">
              <div class="flex items-center justify-between gap-1">
                <span class="text-[11px] font-extrabold font-mono text-amber-300" dir="ltr">${esc(qa.code)}</span>
                <span class="text-[10px] text-slate-400 truncate">${esc(name)}</span>
              </div>
              <div class="text-xs font-extrabold font-mono text-white tabular-nums mt-0.5 truncate" dir="ltr">${esc(val)}</div>
            </button>
          `;
        })
        .join("");
    }
  }

  function renderModalContent() {
    if (!detailState.open) return;
    const appState = getState();
    const isAr = appState.lang === "ar";
    const asset = getAssetObj(detailState.assetId);
    if (!asset) return;

    // 1. Header
    const titleEl = document.getElementById("detail-modal-title");
    const subEl = document.getElementById("detail-modal-subtitle");
    const priceEl = document.getElementById("detail-modal-spot");
    const flagSlot = document.getElementById("detail-modal-flag");

    const localizedName = getLocalizedAssetName(asset);
    if (titleEl) titleEl.textContent = `${localizedName} (${asset.code})`;
    if (subEl) {
      subEl.textContent = detailState.data
        ? isAr
          ? detailState.data.unit_label_ar
          : detailState.data.unit_label_en
        : asset.pairLabel;
    }
    if (priceEl) {
      const isDirectUsdQuote =
        asset.category === "fiat" &&
        asset.id !== "USD" &&
        String(asset.pairLabel || "").toUpperCase().startsWith(`${String(asset.code || "").toUpperCase()}/USD`);
      priceEl.setAttribute("dir", "ltr");
      priceEl.textContent =
        asset.category === "fiat"
          ? asset.id === "USD"
            ? "$1.00 USD"
            : isDirectUsdQuote
            ? `1 ${asset.code} = $${asset.spotDisplay} USD`
            : `1 USD = ${asset.spotDisplay} ${asset.code}`
          : `$${asset.spotDisplay}`;
    }
    if (flagSlot) {
      if (asset.id === "SYP") {
        flagSlot.innerHTML = getSyriaRevolutionFlagSvg("w-[27px] h-[18px] rounded-[3px] ring-1 ring-white/20");
        flagSlot.classList.remove("hidden");
      } else {
        flagSlot.innerHTML = "";
        flagSlot.classList.add("hidden");
      }
    }

    // 2. Multi-Timeframe Analysis (1D / 1W / 1M / 1Y)
    const analysisBox = document.getElementById("detail-analysis-box");
    if (analysisBox) {
      if (detailState.loading) {
        analysisBox.innerHTML = `<div class="p-6 rounded-xl bg-slate-950/70 border border-slate-800 text-xs text-slate-400 text-center">${
          isAr
            ? "جارٍ تحميل التحليل التاريخي (يوم · أسبوع · شهر · سنوي) والرسوم البيانية وأحدث الأخبار..."
            : "Loading 1D / 1W / 1M / 1Y historical analysis, chart & targeted news..."
        }</div>`;
      } else if (!detailState.data) {
        analysisBox.innerHTML = `<div class="p-4 rounded-xl bg-slate-950/70 border border-slate-800 text-xs text-slate-400 text-center">${
          isAr ? "تعذر تحميل التحليل التفصيلي حالياً." : "Detailed history temporarily unavailable."
        }</div>`;
      } else {
        const d = detailState.data;
        const tfs = d.timeframes || {};
        const tfDefs = [
          { key: "1d", ar: "يوم (24س)", en: "1 Day" },
          { key: "1w", ar: "أسبوع", en: "1 Week" },
          { key: "1m", ar: "شهر", en: "1 Month" },
          { key: "1y", ar: "سنوي", en: "1 Year" },
        ];

        const tfCardsHtml = tfDefs
          .map(({ key, ar, en }) => {
            const win = tfs[key];
            const active = detailState.activeTf === key;
            const activeCls = active
              ? "border-amber-400 bg-amber-400/10 ring-1 ring-amber-400/40"
              : "border-slate-800 bg-slate-950/80 hover:border-slate-700";
            if (!win) {
              return `
                <button type="button" data-detail-tf="${key}" class="p-2.5 rounded-xl border ${activeCls} text-start transition-all">
                  <span class="block text-[10px] font-bold text-slate-400">${isAr ? ar : en}</span>
                  <span class="block text-xs font-mono text-slate-500 mt-0.5">—</span>
                </button>
              `;
            }
            const pct = Number(win.change_pct || 0);
            const isUp = pct >= 0;
            const colorCls = pct > 0.05 ? "text-emerald-400" : pct < -0.05 ? "text-rose-500" : "text-slate-300";
            const arrow = pct > 0.05 ? "▲" : pct < -0.05 ? "▼" : "➖";
            const sign = pct > 0 ? "+" : "";
            return `
              <button type="button" data-detail-tf="${key}" class="p-2.5 rounded-xl border ${activeCls} text-start transition-all cursor-pointer">
                <span class="block text-[10px] font-bold ${active ? "text-amber-300" : "text-slate-400"}">${isAr ? ar : en}</span>
                <span class="block text-sm font-extrabold font-mono ${colorCls} mt-0.5" dir="ltr">${arrow} ${sign}${esc(pct)}%</span>
                <span class="block text-[9px] font-mono text-slate-400 mt-0.5 truncate" dir="ltr">L:${esc(win.low)} · H:${esc(win.high)}</span>
              </button>
            `;
          })
          .join("");

        const activeWin = tfs[detailState.activeTf] || tfs["1m"] || tfs["1y"] || tfs["1d"];
        const syncedCurrent = asset.spotDisplay || activeWin?.current;
        const chartHtml = activeWin
          ? `
            <div class="p-3 rounded-xl bg-slate-950/90 border border-slate-800 space-y-2">
              <div class="flex flex-wrap items-center justify-between gap-2 text-[11px] font-mono text-slate-400" dir="ltr">
                <span>${isAr ? "أدنى" : "Low"}: <strong class="text-slate-200">${esc(activeWin.low)}</strong></span>
                <span>${isAr ? "الحالي" : "Now"}: <strong class="text-amber-300">${esc(syncedCurrent)}</strong></span>
                <span>${isAr ? "أعلى" : "High"}: <strong class="text-slate-200">${esc(activeWin.high)}</strong></span>
              </div>
              ${renderSvgChart(activeWin.series, Number(activeWin.change_pct || 0) >= 0)}
            </div>
          `
          : "";

        const tech = d.technicals || {};
        const techPills = [];
        if (tech.rsi14 != null) {
          techPills.push(
            `<span class="px-2 py-0.5 rounded-md bg-slate-900 border border-slate-700 text-[10px] font-mono text-slate-200" dir="ltr">RSI(14): <strong>${esc(tech.rsi14)}</strong></span>`
          );
        }
        if (tech.ema20 != null && tech.ema50 != null) {
          const up = tech.ema20 > tech.ema50;
          techPills.push(
            `<span class="px-2 py-0.5 rounded-md bg-slate-900 border border-slate-700 text-[10px] font-mono ${up ? "text-emerald-400" : "text-rose-500"}" dir="ltr">${up ? "▲" : "▼"} EMA20 ${esc(tech.ema20)} / EMA50 ${esc(tech.ema50)}</span>`
          );
        }

        const summaryText = isAr ? d.analysis?.ar : d.analysis?.en;
        analysisBox.innerHTML = `
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">${tfCardsHtml}</div>
          ${chartHtml}
          ${techPills.length ? `<div class="flex flex-wrap items-center gap-1.5">${techPills.join("")}</div>` : ""}
          <p class="text-xs text-slate-200 leading-relaxed p-3 rounded-xl bg-slate-950/70 border border-slate-800">${esc(summaryText || "")}</p>
        `;
      }
    }

    // 3. Converter right below analysis
    renderDetailConverter();

    // 4. Latest Asset-Specific News at bottom
    const newsBox = document.getElementById("detail-news-list");
    if (newsBox) {
      if (detailState.loading) {
        newsBox.innerHTML = `<div class="text-xs text-slate-400 py-2">${
          isAr ? "جارٍ جلب آخر أخبار هذه العملة..." : "Fetching latest headlines for this asset..."
        }</div>`;
      } else {
        const items = detailState.data?.headlines || [];
        if (!items.length) {
          newsBox.innerHTML = `<div class="p-3 rounded-xl bg-slate-950/60 border border-slate-800 text-xs text-slate-400">${
            isAr ? "لا توجد أخبار عاجلة منفصلة لهذه العملة حالياً." : "No recent asset-specific headlines available right now."
          }</div>`;
        } else {
          newsBox.innerHTML = items
            .map((h) => {
              const dt = String(h.published_at || "").slice(0, 16).replace("T", " ");
              return `
                <a href="${safeUrl(h.url)}" target="_blank" rel="noopener noreferrer"
                  class="block p-3 rounded-xl bg-slate-950/80 hover:bg-slate-800/90 border border-slate-800 hover:border-amber-400/40 transition-colors space-y-1">
                  <div class="flex items-center justify-between gap-2 text-[10px]">
                    <span class="font-bold text-amber-300">${esc(h.source)} ↗</span>
                    <span class="font-mono text-slate-400" dir="ltr">${esc(dt)} UTC</span>
                  </div>
                  <p class="text-xs font-semibold text-slate-100 leading-snug">${esc(h.title)}</p>
                </a>
              `;
            })
            .join("");
        }
      }
    }
  }

  function bindEvents() {
    document.getElementById("btn-close-detail-modal")?.addEventListener("click", closeAssetDetail);
    document.getElementById("modal-asset-detail")?.addEventListener("click", (e) => {
      if (e.target.id === "modal-asset-detail") closeAssetDetail();
    });

    document.getElementById("btn-detail-set-main-base")?.addEventListener("click", () => {
      if (detailState.assetId) {
        setMainBaseAsset(detailState.assetId);
        const isAr = getState().lang === "ar";
        showToast(isAr ? "تم تعيينها كعملة الأساس بالصفحة الرئيسية" : "Set as main converter base");
      }
    });

    document.getElementById("detail-conv-amount")?.addEventListener("input", (e) => {
      const raw = String(e.target.value || "");
      let cleaned = raw.replace(/[^0-9\u0660-\u0669.,\u066B\u066C]/g, "");
      const firstDot = cleaned.search(/[.\u066B]/);
      if (firstDot !== -1) {
        cleaned = cleaned.slice(0, firstDot + 1) + cleaned.slice(firstDot + 1).replace(/[.\u066B]/g, "");
      }
      if (cleaned !== raw) e.target.value = cleaned;
      detailState.convAmount = cleaned;
      renderDetailConverter();
    });

    document.getElementById("detail-conv-target")?.addEventListener("change", (e) => {
      detailState.convTargetId = e.target.value;
      detailState.convSwapped = false;
      renderDetailConverter();
    });

    document.getElementById("btn-detail-conv-swap")?.addEventListener("click", () => {
      // Preserve amount & current modal asset — simply flip conversion direction cleanly!
      detailState.convSwapped = !detailState.convSwapped;
      renderDetailConverter();
    });

    document.addEventListener("click", (e) => {
      const tfBtn = e.target.closest("[data-detail-tf]");
      if (tfBtn && detailState.open) {
        detailState.activeTf = tfBtn.getAttribute("data-detail-tf") || "1d";
        renderModalContent();
        return;
      }

      const pickTargetBtn = e.target.closest("[data-detail-pick-target]");
      if (pickTargetBtn && detailState.open) {
        detailState.convTargetId = pickTargetBtn.getAttribute("data-detail-pick-target") || "USD";
        const sel = document.getElementById("detail-conv-target");
        if (sel) sel.value = detailState.convTargetId;
        renderDetailConverter();
      }
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && detailState.open) {
        closeAssetDetail();
      }
    });
  }

  return {
    init: bindEvents,
    open: openAssetDetail,
    close: closeAssetDetail,
    refreshIfOpen() {
      if (detailState.open && detailState.assetId) {
        const asset = getAssetObj(detailState.assetId);
        if (asset) {
          populateDetailTargetSelect();
          renderModalContent();
          fetchAssetDetailData(asset, getState().lang);
        }
      }
    },
  };
}
