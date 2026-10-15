/* One preference across live, historical and embedded reports. No image filters. */
(() => {
  'use strict';
  if (window.masReportTheme) return;
  const root = document.documentElement, key = 'mas-report-theme';
  const system = matchMedia('(prefers-color-scheme: dark)');
  const valid = value => ['light', 'dark', 'system'].includes(value);
  let preference = 'system';
  try { const saved = localStorage.getItem(key); if (valid(saved)) preference = saved; } catch (_) {}
  function apply(value, save = false) {
    preference = valid(value) ? value : 'system';
    root.dataset.masTheme = preference === 'system' ? (system.matches ? 'dark' : 'light') : preference;
    root.dataset.masThemePreference = preference;
    root.style.colorScheme = root.dataset.masTheme;
    document.querySelectorAll('#mas-theme-toolbar button').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.theme === preference));
    });
    if (save) { try { localStorage.setItem(key, preference); } catch (_) {} }
  }
  window.masReportTheme = { set: value => apply(value, true) };
  apply(preference);
  system.addEventListener('change', () => { if (preference === 'system') apply('system'); });
  addEventListener('storage', event => { if (event.key === key || event.key === null) apply(event.newValue); });

  function start() {
    // Adapt authored paint declarations while preserving historical layouts.
    const sheetNode = document.createElement('style'); sheetNode.id = 'mas-theme-palette';
    const inlineNode = document.createElement('style'); inlineNode.id = 'mas-theme-inline';
    document.head.append(sheetNode, inlineNode);
    const context = document.createElement('canvas').getContext('2d'), cache = new Map();
    function tint(token, role) {
      const id = role + token; if (cache.has(id)) return cache.get(id);
      context.fillStyle = '#010203'; context.fillStyle = token;
      const normalized = context.fillStyle; let rgb, alpha = 1;
      if (normalized.startsWith('#')) rgb = [1, 3, 5].map(i => parseInt(normalized.slice(i, i + 2), 16) / 255);
      else {
        const parts = normalized.match(/[\d.]+/g).map(Number); rgb = parts.slice(0, 3).map(x => x / 255);
        alpha = parts.length > 3 ? parts[3] : 1;
      }
      const hi = Math.max(...rgb), lo = Math.min(...rgb), delta = hi - lo, light = (hi + lo) / 2;
      let hue = 0, saturation = delta ? delta / (1 - Math.abs(2 * light - 1)) : 0;
      if (delta) {
        const [r, g, b] = rgb;
        hue = 60 * (hi === r ? ((g - b) / delta + 6) % 6 : hi === g ? (b - r) / delta + 2 : (r - g) / delta + 4);
      }
      let level;
      if (role === 'background') {
        if (delta < .09 || saturation < .14) {
          hue = 215; saturation = .30;
          level = light > .985 ? .145 : light > .90 ? .105 : light > .65 ? .18 : light > .30 ? .23 : .09;
        } else { saturation = Math.min(.60, Math.max(.25, saturation)); level = light > .72 ? .17 : .29; }
      } else if (role === 'border') {
        level = .32; saturation = Math.min(.38, saturation); if (delta < .09) { hue = 215; saturation = .23; }
      } else if (role === 'shadow') { hue = 220; saturation = .30; level = .025; }
      else {
        if (delta < .14 || (hi < .55 && saturation < .55)) {
          hue = 215; saturation = .28; level = light < .32 || light > .85 ? .91 : .73;
        } else { saturation = Math.min(.78, Math.max(.45, saturation)); level = .74; }
      }
      const value = `hsla(${hue.toFixed(1)},${(saturation * 100).toFixed(1)}%,${(level * 100).toFixed(1)}%,${alpha})`;
      cache.set(id, value); return value;
    }
    function variables(value, element = root) {
      for (let i = 0; i < 6 && value.includes('var('); i++) {
        const next = value.replace(/var\((--[\w-]+)(?:,\s*([^()]*))?\)/g, (all, name, fallback) =>
          getComputedStyle(element).getPropertyValue(name).trim() || fallback || all);
        if (next === value) break; value = next;
      }
      return value;
    }
    function paint(style, element) {
      const result = [];
      // CSSOM exposes empty longhands for shorthands containing var(...).
      const properties = new Set([...style, 'background', 'border', 'border-top', 'border-right', 'border-bottom', 'border-left', 'outline']);
      for (const property of properties) {
        if (!/^(?:color|background(?:-color|-image)?|border(?:-(?:top|right|bottom|left))?(?:-color)?|outline(?:-color)?|box-shadow|text-shadow|text-decoration-color)$/.test(property)) continue;
        const authored = style.getPropertyValue(property);
        if (!authored) continue;
        const original = variables(authored, element);
        const role = property.startsWith('background') ? 'background' : property.includes('shadow') ? 'shadow' : /border|outline/.test(property) ? 'border' : 'foreground';
        const next = original.replace(/url\([^)]*\)|"[^"]*"|'[^']*'|#[\da-f]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)|\b(?:white|black|gray|grey|silver|red|green|blue|navy|teal|orange|yellow|purple|maroon)\b/gi,
          token => /^(?:url\(|["'])/i.test(token) ? token : tint(token, role));
        if (next !== original) result.push(`${property}:${next}!important`);
      }
      return result.join(';');
    }
    function selectors(value) {
      const parts = []; let start = 0, depth = 0, quote = null;
      for (let i = 0; i < value.length; i++) {
        const c = value[i];
        if (quote) { if (c === quote && value[i - 1] !== '\\') quote = null; continue; }
        if (c === '"' || c === "'") quote = c;
        else if (c === '(' || c === '[') depth++;
        else if (c === ')' || c === ']') depth--;
        else if (c === ',' && !depth) { parts.push(value.slice(start, i)); start = i + 1; }
      }
      parts.push(value.slice(start));
      return parts.map(part => {
        const s = part.trim(), prefix = 'html[data-mas-theme="dark"]';
        return /^(?:html|:root)(?=[\s.#[:>+~]|$)/.test(s) ? s.replace(/^(?:html|:root)/, prefix) : prefix + ' ' + s;
      }).join(',');
    }
    function rules(list) {
      return Array.from(list).map(rule => {
        if (rule.selectorText && rule.style) {
          const declarations = paint(rule.style);
          return declarations ? `${selectors(rule.selectorText)}{${declarations}}` : '';
        }
        if (rule.cssRules && /^@(?:media|supports|layer)\b/.test(rule.cssText))
          return rule.cssText.slice(0, rule.cssText.indexOf('{') + 1) + rules(rule.cssRules) + '}';
        return '';
      }).join('\n');
    }
    function rebuild() {
      const css = [];
      for (const sheet of document.styleSheets) {
        if (sheet.ownerNode?.id?.startsWith('mas-theme') || sheet.href?.includes('/_report_theme/')) continue;
        try { css.push(rules(sheet.cssRules)); } catch (_) { /* Cross-origin styles are not inspected. */ }
      }
      sheetNode.textContent = css.join('\n');
    }
    const inlineRules = new Map();
    function inline(element) {
      if (!element.style || element.closest('#mas-theme-toolbar') || element === root) return;
      const declarations = paint(element.style, element);
      if (!declarations) { element.removeAttribute('data-mas-paint'); return; }
      if (!inlineRules.has(declarations)) {
        const id = String(inlineRules.size); inlineRules.set(declarations, id);
        inlineNode.sheet.insertRule(`html[data-mas-theme="dark"] [data-mas-paint="${id}"]{${declarations}}`);
      }
      element.dataset.masPaint = inlineRules.get(declarations);
    }
    function scan(node) {
      if (node.nodeType !== 1 || node.id?.startsWith('mas-theme')) return;
      if (node.hasAttribute('style')) inline(node);
      node.querySelectorAll('[style]').forEach(inline);
    }
    rebuild(); scan(document.body);
    new MutationObserver(changes => {
      let refresh = false;
      for (const change of changes) {
        if (change.target.id?.startsWith('mas-theme')) continue;
        if (change.type === 'attributes') inline(change.target);
        for (const node of change.addedNodes) {
          if (node.id?.startsWith('mas-theme')) continue;
          scan(node);
          if (node.nodeName === 'STYLE') refresh = true;
        }
      }
      if (refresh) rebuild();
    }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['style'] });
    document.addEventListener('load', event => { if (event.target.tagName === 'LINK' && event.target.rel === 'stylesheet') rebuild(); }, true);
    if (window === window.top) {
      const toolbar = document.createElement('div'); toolbar.id = 'mas-theme-toolbar';
      toolbar.innerHTML = '<span>外观</span><div class="mas-theme-options" role="group" aria-label="网页亮暗模式">' +
        [['light', '亮色'], ['dark', '暗色'], ['system', '跟随系统']].map(([value, label]) =>
          `<button type="button" data-theme="${value}" aria-pressed="false">${label}</button>`).join('') + '</div>';
      toolbar.addEventListener('click', event => { const button = event.target.closest('button[data-theme]'); if (button) apply(button.dataset.theme, true); });
      document.body.prepend(toolbar);
    }
    apply(preference); root.dataset.masThemeReady = 'true';
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
