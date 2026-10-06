/* Chronological public conversation and exact tool attachments, independent of playback. */
window.ReplayToolTrace = class ReplayToolTrace {
  constructor(root, data, options = {}) {
    this.root = root;
    this.onReplay = options.onReplay;
    this.onImage = options.onImage;
    this.render(data);
  }

  node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined && text !== null) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  }

  json(value) {
    return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  }

  detail(parent, label, values, className = 'tt-detail') {
    const detail = this.node('details', undefined, className);
    detail.append(this.node('summary', label));
    for (const value of values) {
      if (value !== undefined && value !== null) detail.append(this.node('pre', this.json(value), 'tt-json'));
    }
    parent.append(detail);
    return detail;
  }

  source(parent, entries) {
    const sources = entries.map(entry => Object.fromEntries(
      ['sequence', 'source', 'source_id', 'source_event_index', 'source_line', 'call_id', 'at', 'alignment']
        .filter(key => entry[key] !== undefined && entry[key] !== null)
        .map(key => [key, entry[key]])
    )).filter(source => Object.keys(source).length);
    if (sources.length) this.detail(parent, '来源', sources, 'tt-detail tt-source');
  }

  imageURL(file) {
    if (typeof file !== 'string' || !file.trim()) return null;
    try {
      const base = new URL('.', document.baseURI);
      const url = new URL(file, base);
      if (!['http:', 'https:', 'file:'].includes(url.protocol) || url.origin !== base.origin ||
          !url.pathname.startsWith(base.pathname)) return null;
      return url.href;
    } catch {
      return null;
    }
  }

  verified(image) {
    return ['matched_archive_sha256', 'raw_attachment_sha256'].includes(image?.verification);
  }

  imageFigure(image, entry, target = null) {
    const url = this.imageURL(image.file);
    if (!url || !this.verified(image)) return null;
    const figure = this.node('figure', undefined, 'tt-figure');
    figure.dataset.imageRef = image.image_ref || '';
    figure.dataset.verification = image.verification;
    const wrap = this.node('div', undefined, 'tt-overlay-wrap');
    const link = this.node('a', undefined, 'tt-image-link');
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener';
    link.setAttribute('aria-label', `打开${image.view || ''}原图`);
    link.addEventListener('click', event => {
      event.stopPropagation();
      if (typeof this.onImage === 'function') {
        event.preventDefault();
        this.onImage(image, {entry, url, target});
      }
    });
    const picture = this.node('img', undefined, 'tt-image');
    picture.src = url;
    picture.alt = `${image.view || 'RGB'} · ${image.image_ref || '工具返回原图'}`;
    picture.loading = 'lazy';
    picture.decoding = 'async';
    if ([image.width, image.height].every(value => Number.isInteger(value) && value > 0)) {
      picture.width = image.width;
      picture.height = image.height;
    }
    picture.addEventListener('error', () => {
      picture.hidden = true;
      if (!wrap.querySelector('.tt-image-error')) wrap.append(this.node('span', '原图读取失败', 'tt-note tt-image-error'));
    });
    link.append(picture);
    wrap.append(link);
    if (target) {
      const marker = this.node('span', undefined, 'tt-point-marker');
      marker.style.left = `${target.point[0] * 100}%`;
      marker.style.top = `${target.point[1] * 100}%`;
      marker.setAttribute('aria-hidden', 'true');
      wrap.append(marker);
      figure.dataset.point = JSON.stringify(target.point);
    }
    const caption = target
      ? `${image.view || 'RGB'} · 调用前原图；红圈为模型选点，不是机器人最终位置`
      : `${image.view || 'RGB'} · 工具返回原图${image.image_ref ? ' · ' + image.image_ref : ''}`;
    figure.append(wrap, this.node('figcaption', caption, 'tt-caption'));
    return figure;
  }

  navigationTarget(card, entry) {
    const arguments_ = entry.arguments;
    if (!arguments_ || arguments_.primitive !== 'navigate_to') return;
    const target = arguments_.target;
    const point = target?.point;
    if (!Array.isArray(point) || point.length !== 2 ||
        !point.every(value => Number.isFinite(value) && value >= 0 && value <= 1)) return;
    const image = this.previousImages.get(target.image_ref);
    if (!image) {
      card.append(this.node('p', '模型选点对应的先前工具返回原图未核验，无法标注。', 'tt-note'));
      return;
    }
    const figure = this.imageFigure(image, entry, target);
    if (figure) {
      const media = this.node('div', undefined, 'tt-media tt-target');
      media.append(figure);
      card.append(media);
    }
  }

  result(card, entry) {
    const texts = Array.isArray(entry.text_blocks) ? entry.text_blocks : [];
    const values = [...texts];
    if (entry.structured_content !== undefined && entry.structured_content !== null) values.push(entry.structured_content);
    if (entry.error !== undefined && entry.error !== null) values.push(entry.error);
    let payload = entry.structured_content;
    for (const text of texts) {
      try {
        const parsed = JSON.parse(text);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) { payload = parsed; break; }
      } catch { /* Preserve non-JSON result text verbatim below. */ }
    }
    if (entry.error || entry.is_error || entry.status === 'failed' || payload?.ok === false) {
      card.append(this.node('p', '工具调用失败', 'tt-note tt-error'));
    }
    this.detail(card, '工具返回 · 完整原文', values);
    const media = this.node('div', undefined, 'tt-media');
    let shown = 0;
    for (const image of Array.isArray(entry.images) ? entry.images : []) {
      const figure = this.imageFigure(image, entry);
      if (!figure) continue;
      media.append(figure);
      shown += 1;
      if (image.image_ref) this.previousImages.set(image.image_ref, image);
    }
    if (shown) card.append(media);
    const recorded = entry.image_delivery?.attachment_count ?? entry.image_count;
    if (Number.isFinite(recorded) && recorded > shown) {
      card.append(this.node('p', `原始记录有 ${recorded} 个图像附件，${shown} 个已核验且可显示。`, 'tt-note tt-diagnostic'));
    }
    if (entry.image_delivery?.status && entry.image_delivery.status !== 'verified') {
      card.append(this.node('p', '图像附件记录不完整；未用后台观测补图。', 'tt-note tt-diagnostic'));
    }
    if (entry.image_delivery?.diagnostics?.length) {
      this.detail(card, '图像附件核验记录', [entry.image_delivery], 'tt-detail tt-diagnostic');
    }
  }

  replayLink(header, step) {
    if (step === undefined || step === null || typeof this.onReplay !== 'function') return;
    const button = this.node('button', '回放此步', 'tt-replay');
    button.type = 'button';
    button.addEventListener('click', event => { event.stopPropagation(); this.onReplay(step); });
    header.append(button);
  }

  render(data = this.data) {
    this.data = data || {};
    this.root.classList.add('tt-feed');
    this.root.setAttribute('aria-label', '完整模型与工具会话');
    this.root.replaceChildren();
    this.previousImages = new Map();
    const entries = Array.isArray(this.data.tool_trace) ? this.data.tool_trace : (this.data.model_transcript || []);
    const steps = new Map((this.data.steps || []).map(step => [step.index, step]));
    const calls = new Map(entries.filter(entry => entry.kind === 'tool_call' && entry.call_id).map(entry => [entry.call_id, entry]));
    const returned = new Set(entries.filter(entry => entry.kind === 'tool_result' && entry.call_id).map(entry => entry.call_id));
    for (let index = 0; index < entries.length; index += 1) {
      const entry = entries[index];
      const next = entries[index + 1];
      const paired = entry.kind === 'tool_call' && entry.call_id && next?.kind === 'tool_result' && next.call_id === entry.call_id;
      const result = paired ? next : entry.kind === 'tool_result' ? entry : null;
      const message = ['assistant', 'provider_summary'].includes(entry.kind);
      const card = this.node('article', undefined, `tt-entry ${entry.kind === 'assistant' ? 'tt-agent' : entry.kind === 'provider_summary' ? 'tt-summary' : 'tt-tool'}`);
      card.dataset.kind = paired ? 'tool' : entry.kind;
      if (entry.sequence !== undefined) card.dataset.sequence = entry.sequence;
      if (paired) card.dataset.resultSequence = result.sequence;
      if (entry.step !== undefined && entry.step !== null) card.dataset.step = entry.step;
      if (entry.call_id) card.dataset.callId = entry.call_id;
      const header = this.node('div', undefined, 'tt-head');
      if (message) {
        header.append(this.node('strong', entry.kind === 'assistant' ? '模型输出' : '推理摘要 · 接口原文', 'tt-label'));
      } else {
        header.append(this.node('span', entry.kind === 'tool_result' ? 'tool result' : 'tool', 'tt-badge'));
        const arguments_ = entry.arguments || calls.get(entry.call_id)?.arguments;
        const detail = arguments_?.primitive || (entry.tool === 'read_skill' ? arguments_?.name : null);
        const label = (entry.tool || '工具记录') + (detail ? ' · ' + detail : '');
        header.append(this.node('strong', label, 'tt-tool-name'));
        if (result) {
          const count = result.image_delivery?.attachment_count ?? result.image_count;
          if (Number.isFinite(count)) header.append(this.node('span', `${count} 张图像`, 'tt-count'));
          const step = steps.get(result.step ?? entry.step);
          if (step?.tool === entry.tool && Number.isFinite(step.tool_seconds) && step.tool_seconds >= 0) {
            const duration = this.node('span', `工具 ${step.tool_seconds.toFixed(1)} s`, 'tt-duration');
            duration.title = '服务端记录的工具耗时';
            header.append(duration);
          }
        }
      }
      this.replayLink(header, entry.step ?? result?.step);
      card.append(header);
      if (message) {
        card.append(this.node('div', entry.text ?? '', 'tt-original'));
      } else {
        if (entry.kind === 'tool_call') {
          this.detail(card, '调用参数', [entry.arguments]);
          this.navigationTarget(card, entry);
          if (!paired && !returned.has(entry.call_id)) card.append(this.node('p', '未记录工具返回', 'tt-note tt-error'));
        }
        if (result) this.result(card, result);
      }
      this.source(card, paired ? [entry, result] : [entry]);
      this.root.append(card);
      if (paired) index += 1;
    }
    if (!entries.length) {
      this.root.append(this.node('p', '尚无已归档的模型与工具会话。', 'tt-empty'));
      if (this.data.failure) this.root.append(this.node('pre', this.json(this.data.failure), 'tt-json tt-error'));
    }
    return this;
  }
};
