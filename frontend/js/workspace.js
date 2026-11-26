/**
 * RatePath (ريت باث) — Personal Workspace State Engine (`ratepath_workspace_v1`)
 *
 * Manages visitor-defined ordering, custom CoinMarketCap & 155+ FX additions,
 * hidden/deleted built-in defaults, drag-and-drop reordering, and instant zero-login
 * persistence across `localStorage` + `Telegram.WebApp.CloudStorage`.
 */

export const WORKSPACE_STORAGE_KEY = "ratepath_workspace_v1";
export const WORKSPACE_VERSION = 2;

export const DEFAULT_WORKSPACE_IDS = Object.freeze([
  "USD",
  "EUR",
  "GBP",
  "SAR",
  "AED",
  "EGP",
  "TRY",
  "SYP",
  "XAU_OZ",
  "GRAM_24K",
  "GRAM_21K",
  "GRAM_18K",
  "XAG_OZ",
  "XAG_GRAM",
  "BTC",
  "ETH",
  "SOL",
]);

const METAL_IDS = new Set(["XAU_OZ", "GRAM_24K", "GRAM_21K", "GRAM_18K", "XAG_OZ", "XAG_GRAM"]);
const CORE_CRYPTO_IDS = new Set(["BTC", "ETH", "SOL"]);

const DEFAULT_ID_SET = new Set(DEFAULT_WORKSPACE_IDS);
const MAX_WORKSPACE_ASSETS = 80;
const FORBIDDEN_KEYS = new Set(["__proto__", "constructor", "prototype"]);

export function createDefaultWorkspace() {
  return {
    version: WORKSPACE_VERSION,
    ordered_ids: [...DEFAULT_WORKSPACE_IDS],
    added_custom_assets: {},
    hidden_defaults: [],
    updated_at: new Date().toISOString(),
  };
}

export function normalizeWorkspace(raw) {
  if (!raw || typeof raw !== "object") {
    return createDefaultWorkspace();
  }

  const hiddenSet = new Set(
    Array.isArray(raw.hidden_defaults)
      ? raw.hidden_defaults
          .map((id) => String(id || "").trim())
          .filter((id) => DEFAULT_ID_SET.has(id))
      : []
  );

  const customMap = Object.create(null);
  if (raw.added_custom_assets && typeof raw.added_custom_assets === "object") {
    let customCount = 0;
    for (const [k, v] of Object.entries(raw.added_custom_assets)) {
      if (customCount >= MAX_WORKSPACE_ASSETS) break;
      const cleanId = String(k || "").trim().slice(0, 48);
      if (!cleanId || FORBIDDEN_KEYS.has(cleanId) || !v || typeof v !== "object") continue;
      customMap[cleanId] = { ...v, id: cleanId };
      customCount += 1;
    }
  }

  const seen = new Set();
  const ordered = [];
  if (Array.isArray(raw.ordered_ids)) {
    for (const item of raw.ordered_ids) {
      if (ordered.length >= MAX_WORKSPACE_ASSETS) break;
      const cleanId = String(item || "").trim().slice(0, 48);
      if (!cleanId || FORBIDDEN_KEYS.has(cleanId) || seen.has(cleanId) || hiddenSet.has(cleanId)) continue;
      seen.add(cleanId);
      ordered.push(cleanId);
    }
  } else {
    for (const defId of DEFAULT_WORKSPACE_IDS) {
      if (!hiddenSet.has(defId)) {
        seen.add(defId);
        ordered.push(defId);
      }
    }
  }

  // Ensure any non-hidden default added in future updates appears unless explicitly hidden
  // ONLY if the user has never customized `ordered_ids` (empty array with no hidden defaults)
  if (ordered.length === 0 && hiddenSet.size === 0 && Object.keys(customMap).length === 0) {
    return createDefaultWorkspace();
  }

  // Seamless v1 -> v2 upgrade: add live Silver (XAG_OZ & XAG_GRAM) right after Gold for existing users
  const rawVersion = Number(raw.version || 1);
  if (rawVersion < WORKSPACE_VERSION) {
    for (const silverId of ["XAG_GRAM", "XAG_OZ"]) {
      if (!seen.has(silverId) && !hiddenSet.has(silverId) && ordered.length < MAX_WORKSPACE_ASSETS) {
        const goldAnchorIdx = ordered.indexOf("GRAM_18K");
        const xauIdx = ordered.indexOf("XAU_OZ");
        const insertAt = goldAnchorIdx !== -1 ? goldAnchorIdx + 1 : xauIdx !== -1 ? xauIdx + 1 : ordered.length;
        ordered.splice(insertAt, 0, silverId);
        seen.add(silverId);
      }
    }
  }

  return {
    version: WORKSPACE_VERSION,
    ordered_ids: ordered,
    added_custom_assets: { ...customMap },
    hidden_defaults: [...hiddenSet],
    updated_at:
      typeof raw.updated_at === "string" && raw.updated_at.trim()
        ? raw.updated_at
        : new Date().toISOString(),
  };
}

export function loadWorkspaceLocal() {
  try {
    const raw = localStorage.getItem(WORKSPACE_STORAGE_KEY);
    if (!raw) return createDefaultWorkspace();
    return normalizeWorkspace(JSON.parse(raw));
  } catch {
    return createDefaultWorkspace();
  }
}

function getTelegramCloudStorage() {
  try {
    const tg = window.Telegram?.WebApp;
    // CloudStorage exists only inside real Telegram clients supporting Bot API >= 6.9
    if (!tg || !tg.initData || typeof tg.isVersionAtLeast !== "function" || !tg.isVersionAtLeast("6.9")) {
      return null;
    }
    const cloud = tg.CloudStorage;
    return cloud && typeof cloud.getItem === "function" ? cloud : null;
  } catch {
    return null;
  }
}

export function saveWorkspaceLocal(workspace) {
  const clean = normalizeWorkspace(workspace);
  const serialized = JSON.stringify(clean);
  try {
    localStorage.setItem(WORKSPACE_STORAGE_KEY, serialized);
  } catch {
    // Ignore private-mode storage limits
  }

  // Mirror automatically to Telegram Mini App CloudStorage when available
  try {
    const cloud = getTelegramCloudStorage();
    if (cloud && typeof cloud.setItem === "function" && serialized.length <= 4000) {
      cloud.setItem(WORKSPACE_STORAGE_KEY, serialized);
    }
  } catch {
    // Non-Telegram browser or CloudStorage unavailable
  }
  return clean;
}

export function loadWorkspaceFromTelegramCloud() {
  return new Promise((resolve) => {
    try {
      const cloud = getTelegramCloudStorage();
      if (!cloud) {
        resolve(null);
        return;
      }
      cloud.getItem(WORKSPACE_STORAGE_KEY, (err, value) => {
        if (err || !value) {
          resolve(null);
          return;
        }
        try {
          resolve(normalizeWorkspace(JSON.parse(value)));
        } catch {
          resolve(null);
        }
      });
    } catch {
      resolve(null);
    }
  });
}

export function isWorkspaceCustomized(workspace) {
  if (!workspace) return false;
  if ((workspace.hidden_defaults || []).length > 0) return true;
  if (Object.keys(workspace.added_custom_assets || {}).length > 0) return true;
  const ids = workspace.ordered_ids || [];
  if (ids.length !== DEFAULT_WORKSPACE_IDS.length) return true;
  return ids.some((id, idx) => id !== DEFAULT_WORKSPACE_IDS[idx]);
}

function inferAssetCategory(id, customAssets = {}) {
  if (METAL_IDS.has(id)) return "gold";
  if (CORE_CRYPTO_IDS.has(id) || /^(CMC_|CG_|DEX_|BN_|CDN_)/.test(id)) return "crypto";
  const custom = customAssets?.[id];
  if (custom?.category) return custom.category;
  return "fiat";
}

export function addAssetToWorkspace(workspace, assetDescriptor, { pinTop = false, autoGroup = true } = {}) {
  const ws = normalizeWorkspace(workspace);
  const id = String(assetDescriptor?.id || "").trim();
  if (!id) return ws;

  const hidden = ws.hidden_defaults.filter((x) => x !== id);
  const customAssets = { ...ws.added_custom_assets };

  if (!DEFAULT_ID_SET.has(id)) {
    customAssets[id] = {
      id,
      cmc_id: assetDescriptor.cmc_id ?? null,
      code: assetDescriptor.code || assetDescriptor.symbol || id,
      symbol: assetDescriptor.symbol || assetDescriptor.code || id,
      name: assetDescriptor.name || assetDescriptor.name_en || id,
      name_en: assetDescriptor.name_en || assetDescriptor.name || id,
      name_ar: assetDescriptor.name_ar || assetDescriptor.name || id,
      price_usd: assetDescriptor.price_usd || null,
      units_per_usd: assetDescriptor.units_per_usd || null,
      change_24h_pct: assetDescriptor.change_24h_pct || "0.00",
      logo_url: assetDescriptor.logo_url || "",
      rank: assetDescriptor.rank ?? null,
      category: assetDescriptor.category || (assetDescriptor.units_per_usd ? "fiat" : "crypto"),
    };
  }

  let ordered = ws.ordered_ids.filter((x) => x !== id);
  if (pinTop) {
    ordered = [id, ...ordered];
  } else if (autoGroup) {
    // Automatically insert the new asset cleanly at the end of its matching category group
    // (Fiat with Fiat, Gold/Silver with Metals, Crypto with Crypto) so the page stays neatly organized!
    const targetCat = assetDescriptor.category || inferAssetCategory(id, customAssets);
    let lastMatchingIdx = -1;
    for (let i = 0; i < ordered.length; i++) {
      if (inferAssetCategory(ordered[i], customAssets) === targetCat) {
        lastMatchingIdx = i;
      }
    }
    if (lastMatchingIdx !== -1) {
      ordered.splice(lastMatchingIdx + 1, 0, id);
    } else if (targetCat === "fiat") {
      ordered.unshift(id);
    } else {
      ordered.push(id);
    }
  } else {
    ordered = [...ordered, id];
  }

  return {
    ...ws,
    version: WORKSPACE_VERSION,
    ordered_ids: ordered,
    added_custom_assets: customAssets,
    hidden_defaults: hidden,
    updated_at: new Date().toISOString(),
  };
}

export function removeAssetFromWorkspace(workspace, assetId) {
  const ws = normalizeWorkspace(workspace);
  const id = String(assetId || "").trim();
  if (!id) return ws;

  const ordered = ws.ordered_ids.filter((x) => x !== id);
  const hiddenSet = new Set(ws.hidden_defaults);
  const customAssets = { ...ws.added_custom_assets };

  if (DEFAULT_ID_SET.has(id)) {
    hiddenSet.add(id);
  } else {
    delete customAssets[id];
  }

  return {
    ...ws,
    ordered_ids: ordered,
    added_custom_assets: customAssets,
    hidden_defaults: [...hiddenSet],
    updated_at: new Date().toISOString(),
  };
}

export function moveAssetByDelta(workspace, assetId, delta) {
  const ws = normalizeWorkspace(workspace);
  const ordered = [...ws.ordered_ids];
  const idx = ordered.indexOf(assetId);
  if (idx === -1) return ws;
  const targetIdx = idx + delta;
  if (targetIdx < 0 || targetIdx >= ordered.length) return ws;

  const [item] = ordered.splice(idx, 1);
  ordered.splice(targetIdx, 0, item);

  return {
    ...ws,
    ordered_ids: ordered,
    updated_at: new Date().toISOString(),
  };
}

export function pinAssetToTop(workspace, assetId) {
  const ws = normalizeWorkspace(workspace);
  const idx = ws.ordered_ids.indexOf(assetId);
  if (idx <= 0) return ws;
  const ordered = [assetId, ...ws.ordered_ids.filter((x) => x !== assetId)];
  return {
    ...ws,
    ordered_ids: ordered,
    updated_at: new Date().toISOString(),
  };
}

export function reorderAssetBefore(workspace, draggedId, targetId) {
  const ws = normalizeWorkspace(workspace);
  if (!draggedId || !targetId || draggedId === targetId) return ws;

  const ordered = [...ws.ordered_ids];
  const fromIdx = ordered.indexOf(draggedId);
  const toIdx = ordered.indexOf(targetId);
  if (fromIdx === -1 || toIdx === -1) return ws;

  const [moved] = ordered.splice(fromIdx, 1);
  ordered.splice(toIdx, 0, moved);

  return {
    ...ws,
    ordered_ids: ordered,
    updated_at: new Date().toISOString(),
  };
}

export function resetWorkspaceDefaults() {
  return createDefaultWorkspace();
}

/**
 * Smart two-way merge when loading cloud preferences from Supabase `public.user_preferences`.
 * Preserves the newer layout's exact ordering while keeping custom assets added on either device.
 */
export function mergeWorkspaces(localRaw, cloudRaw) {
  const localWs = normalizeWorkspace(localRaw);
  if (!cloudRaw || typeof cloudRaw !== "object") return localWs;
  const cloudWs = normalizeWorkspace(cloudRaw);

  const localTime = Date.parse(localWs.updated_at || "0") || 0;
  const cloudTime = Date.parse(cloudWs.updated_at || "0") || 0;

  // If local is untouched default, always adopt cloud layout immediately
  if (!isWorkspaceCustomized(localWs) && isWorkspaceCustomized(cloudWs)) {
    return cloudWs;
  }

  const primary = cloudTime >= localTime ? cloudWs : localWs;
  const secondary = cloudTime >= localTime ? localWs : cloudWs;

  const mergedCustom = {
    ...secondary.added_custom_assets,
    ...primary.added_custom_assets,
  };
  const hiddenSet = new Set(primary.hidden_defaults);
  const mergedOrder = [...primary.ordered_ids];
  const seen = new Set(mergedOrder);

  for (const id of Object.keys(mergedCustom)) {
    if (!seen.has(id) && !hiddenSet.has(id)) {
      mergedOrder.push(id);
      seen.add(id);
    }
  }

  return normalizeWorkspace({
    version: 1,
    ordered_ids: mergedOrder,
    added_custom_assets: mergedCustom,
    hidden_defaults: [...hiddenSet],
    updated_at: new Date().toISOString(),
  });
}
