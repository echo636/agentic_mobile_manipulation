/* Shared by the standalone replay and the experiment results page. */
window.ReplayTranscript = class ReplayTranscript {
  constructor(root, onSelect) {
    this.root = root;
    this.onSelect = onSelect;
    this.entries = [];
    this.key = null;
    const make = (tag, text, cls) => {
      const el = document.createElement(tag);
      if (text !== undefined) el.textContent = text;
      if (cls) el.className = cls;
      return el;
    };
    this.make = make;
    root.classList.add('conversation');
    const bar = make('div', undefined, 'conversation-controls');
    bar.append(make('strong', '完整会话 · 按原始顺序'));
    const label = make('label');
    this.follow = make('input'); this.follow.type = 'checkbox'; this.follow.checked = true;
    this.follow.setAttribute('aria-label', '跟随当前播放');
    label.append(this.follow, make('span', '跟随当前播放')); bar.append(label);
    this.count = make('small'); bar.append(this.count);
    this.feed = make('div', undefined, 'conversation-feed');
    this.feed.tabIndex = 0; this.feed.setAttribute('aria-label', '完整模型与工具会话');
    root.replaceChildren(bar, this.feed);
    // Reading history should never fight the video clock. Re-enable explicitly.
    for (const event of ['wheel', 'touchstart']) this.feed.addEventListener(event, () => { this.follow.checked = false; }, {passive:true});
    this.feed.addEventListener('keydown', e => {
      if (['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(e.key)) this.follow.checked = false;
    });
    this.follow.onchange = () => { if (this.follow.checked) this.scrollCurrent(); };
  }
  setData(data) {
    const make = this.make;
    this.feed.replaceChildren(); this.entries = []; this.key = null;
    this.follow.checked = true;
    const labels = {assistant:'Assistant output', provider_summary:'Provider reasoning summary', tool_call:'Tool call', tool_result:'Tool result'};
    for (const entry of data.model_transcript || []) {
      const card = make('article', undefined, 'conversation-entry kind-' + entry.kind);
      card.dataset.sequence = entry.sequence; card.dataset.kind = entry.kind;
      if (entry.step != null) card.dataset.step = entry.step;
      const heading = make('div', undefined, 'conversation-heading');
      heading.append(make('strong', String(entry.sequence).padStart(3,'0') + ' · ' + (labels[entry.kind] || entry.kind)));
      if (entry.step != null) {
        const seek = make('button', '↗ Step ' + entry.step);
        seek.type = 'button'; seek.onclick = () => this.onSelect(entry.step);
        heading.append(seek);
      }
      card.append(heading);
      const source = entry.source === 'model_events.jsonl'
        ? `${entry.source_id || ''} · source event ${entry.source_event_index}`
        : `${entry.at || ''} · aligned to next tool by timestamp`;
      card.append(make('small', source, 'conversation-source'));
      if (entry.kind === 'assistant' || entry.kind === 'provider_summary') {
        card.append(make('pre', entry.text, 'conversation-original'));
      } else if (entry.kind === 'tool_call') {
        card.append(make('b', entry.tool), make('pre', JSON.stringify(entry.arguments, null, 2), 'conversation-arguments'));
      } else {
        card.append(make('b', entry.tool + (entry.status ? ' · ' + entry.status : '')));
        for (const text of entry.text_blocks || []) card.append(make('pre', text, 'conversation-result'));
        if (entry.error != null) card.append(make('pre', JSON.stringify(entry.error, null, 2), 'conversation-error'));
        if (entry.structured_content != null) card.append(make('pre', JSON.stringify(entry.structured_content, null, 2), 'conversation-result'));
        if (entry.image_count) card.append(make('small', entry.image_count + ' returned images · select this step to inspect the archived RGB'));
        if (!entry.text_blocks?.length && entry.error == null && entry.structured_content == null) card.append(make('p', 'No text result recorded.'));
      }
      this.feed.append(card); this.entries.push({entry, card});
    }
    this.count.textContent = this.entries.length + ' 条记录 · 向上滚动可查看全部历史';
    if (!this.entries.length) this.feed.append(make('p', '没有已归档的模型会话。'));
    this.feed.scrollTop = 0;
  }
  setActive(step, phase = 'decision') {
    const key = String(step) + ':' + phase;
    if (key === this.key) return;
    this.key = key;
    const matching = [];
    for (const pair of this.entries) {
      const active = phase === 'final' ? pair.entry.step == null && ['assistant','provider_summary'].includes(pair.entry.kind) : pair.entry.step === step;
      pair.card.classList.toggle('is-current', active);
      if (active) { pair.card.setAttribute('aria-current', 'true'); matching.push(pair); }
      else pair.card.removeAttribute('aria-current');
    }
    this.current = phase === 'result' ? matching.find(p => p.entry.kind === 'tool_result')?.card : matching[0]?.card;
    if (this.follow.checked) this.scrollCurrent();
  }
  scrollCurrent() {
    if (!this.current) return;
    // Scroll only the conversation, never jump the entire page away from video.
    const top = this.current.getBoundingClientRect().top - this.feed.getBoundingClientRect().top + this.feed.scrollTop;
    this.feed.scrollTop = Math.max(0, top - this.feed.clientHeight * .35);
  }
};

// The inspection MP4 has five recorded views in its left 936px. Display them
// beside the full HTML conversation; keep the complete MP4 available to download.
window.ReplayCameraVideo = class ReplayCameraVideo {
  constructor(video) {
    this.video = video;
    this.frame = document.createElement('div'); this.frame.className = 'conversation-video-frame';
    video.before(this.frame); this.frame.append(video);
    this.controls = document.createElement('div'); this.controls.className = 'conversation-video-controls';
    this.button = document.createElement('button'); this.button.type = 'button';
    this.button.textContent = '▶ 播放视频';
    this.button.onclick = () => {
      if (!video.paused) { video.pause(); return; }
      video.play().catch(error => {
        // A quick pause/seek can cancel an asynchronous play request normally.
        if (error.name !== 'AbortError') this.time.textContent = 'Playback failed: ' + error.message;
      });
    };
    this.seek = document.createElement('input'); this.seek.type = 'range';
    this.seek.min = 0; this.seek.max = 1; this.seek.step = .1; this.seek.value = 0;
    this.seek.setAttribute('aria-label', '连续视频进度');
    this.seek.oninput = () => {video.dispatchEvent(new Event('pointerdown')); video.currentTime = Number(this.seek.value);};
    this.time = document.createElement('span');
    this.controls.append(this.button, this.seek, this.time); this.frame.after(this.controls);
    for (const name of ['loadedmetadata','timeupdate','play','pause']) video.addEventListener(name, () => {
      this.seek.max = Number.isFinite(video.duration) ? video.duration : 1;
      this.seek.value = video.currentTime;
      this.button.textContent = video.paused ? '▶ 播放视频' : 'Ⅱ 暂停视频';
      this.time.textContent = video.currentTime.toFixed(1) + ' / ' + (Number.isFinite(video.duration) ? video.duration.toFixed(1) : '—') + ' s';
    });
  }
  setInspection(enabled) {
    this.frame.classList.toggle('camera-region', enabled);
    this.video.controls = !enabled; this.controls.hidden = !enabled;
  }
};
