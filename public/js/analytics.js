/**
 * Analytics — Token usage, cost tracking, and charts
 */

const Analytics = {
  loaded: false,

  async load() {
    const container = document.getElementById('view-analytics');
    if (!this.loaded) {
      container.innerHTML = this._layoutHTML();
      this.loaded = true;
    }
    await this.fetchData();
  },

  _layoutHTML() {
    return `
      <div class="analytics-grid" id="analytics-cards"></div>
      <div class="chart-container">
        <div class="chart-title">Sessions & Cost (Last 30 Days)</div>
        <canvas class="chart-canvas" id="chart-daily"></canvas>
      </div>
      <div class="chart-container">
        <div class="chart-title">Token Usage Breakdown</div>
        <canvas class="chart-canvas" id="chart-tokens"></canvas>
      </div>
    `;
  },

  async fetchData() {
    try {
      const [summaryResp, dailyResp] = await Promise.all([
        fetch('/api/analytics/summary'),
        fetch('/api/analytics/daily?days=30'),
      ]);
      const summary = await summaryResp.json();
      const daily = await dailyResp.json();

      this.renderCards(summary);
      this.renderDailyChart(daily.days || []);
      this.renderTokenChart(summary);
    } catch (e) {
      console.error('Failed to load analytics:', e);
    }
  },

  renderCards(summary) {
    const cards = document.getElementById('analytics-cards');
    const totalTokens = (summary.total_input_tokens || 0) + (summary.total_output_tokens || 0) + (summary.total_cache_tokens || 0);

    cards.innerHTML = `
      <div class="analytics-card">
        <div class="analytics-card-label">Total Sessions</div>
        <div class="analytics-card-value">${summary.total_sessions || 0}</div>
      </div>
      <div class="analytics-card">
        <div class="analytics-card-label">Active Sessions</div>
        <div class="analytics-card-value">${summary.active_sessions || 0}</div>
      </div>
      <div class="analytics-card">
        <div class="analytics-card-label">Total Cost</div>
        <div class="analytics-card-value">$${(summary.total_cost || 0).toFixed(2)}</div>
      </div>
      <div class="analytics-card">
        <div class="analytics-card-label">Today's Cost</div>
        <div class="analytics-card-value">$${(summary.today_cost || 0).toFixed(2)}</div>
      </div>
      <div class="analytics-card">
        <div class="analytics-card-label">Total Tokens</div>
        <div class="analytics-card-value">${this._formatNumber(totalTokens)}</div>
      </div>
      <div class="analytics-card">
        <div class="analytics-card-label">Input Tokens</div>
        <div class="analytics-card-value">${this._formatNumber(summary.total_input_tokens || 0)}</div>
      </div>
      <div class="analytics-card">
        <div class="analytics-card-label">Output Tokens</div>
        <div class="analytics-card-value">${this._formatNumber(summary.total_output_tokens || 0)}</div>
      </div>
      <div class="analytics-card">
        <div class="analytics-card-label">Cache Tokens</div>
        <div class="analytics-card-value">${this._formatNumber(summary.total_cache_tokens || 0)}</div>
      </div>
    `;
  },

  renderDailyChart(days) {
    const canvas = document.getElementById('chart-daily');
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();

    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = rect.height;
    const padding = { top: 20, right: 20, bottom: 40, left: 50 };
    const chartW = w - padding.left - padding.right;
    const chartH = h - padding.top - padding.bottom;

    // Clear
    ctx.clearRect(0, 0, w, h);

    if (!days.length) {
      ctx.fillStyle = '#5a6577';
      ctx.font = '14px -apple-system, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('No data yet', w / 2, h / 2);
      return;
    }

    // Sort by date
    days.sort((a, b) => a.day.localeCompare(b.day));

    const maxSessions = Math.max(...days.map(d => d.session_count), 1);
    const maxCost = Math.max(...days.map(d => d.cost), 0.01);

    // Draw grid lines
    ctx.strokeStyle = '#2a2a4a';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = padding.top + (chartH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(w - padding.right, y);
      ctx.stroke();
    }

    // Draw bars (sessions)
    const barWidth = Math.max(4, (chartW / days.length) - 4);
    ctx.fillStyle = 'rgba(124, 58, 237, 0.6)';

    days.forEach((d, i) => {
      const x = padding.left + (i / days.length) * chartW + 2;
      const barH = (d.session_count / maxSessions) * chartH;
      ctx.fillRect(x, padding.top + chartH - barH, barWidth, barH);
    });

    // Draw cost line
    ctx.strokeStyle = '#22c55e';
    ctx.lineWidth = 2;
    ctx.beginPath();
    days.forEach((d, i) => {
      const x = padding.left + (i / days.length) * chartW + barWidth / 2;
      const y = padding.top + chartH - (d.cost / maxCost) * chartH;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // X-axis labels
    ctx.fillStyle = '#5a6577';
    ctx.font = '10px -apple-system, sans-serif';
    ctx.textAlign = 'center';
    const step = Math.max(1, Math.floor(days.length / 7));
    days.forEach((d, i) => {
      if (i % step === 0) {
        const x = padding.left + (i / days.length) * chartW + barWidth / 2;
        ctx.fillText(d.day.slice(5), x, h - 10);
      }
    });

    // Y-axis labels
    ctx.textAlign = 'right';
    for (let i = 0; i <= 4; i++) {
      const y = padding.top + (chartH / 4) * i;
      const val = Math.round(maxSessions * (1 - i / 4));
      ctx.fillText(val.toString(), padding.left - 8, y + 4);
    }

    // Legend
    ctx.fillStyle = 'rgba(124, 58, 237, 0.6)';
    ctx.fillRect(w - 150, 8, 12, 12);
    ctx.fillStyle = '#8892a4';
    ctx.textAlign = 'left';
    ctx.font = '11px -apple-system, sans-serif';
    ctx.fillText('Sessions', w - 134, 18);

    ctx.strokeStyle = '#22c55e';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(w - 80, 14);
    ctx.lineTo(w - 68, 14);
    ctx.stroke();
    ctx.fillText('Cost', w - 64, 18);
  },

  renderTokenChart(summary) {
    const canvas = document.getElementById('chart-tokens');
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();

    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = rect.height;
    const centerX = w / 2;
    const centerY = h / 2;
    const radius = Math.min(w, h) / 2 - 40;

    ctx.clearRect(0, 0, w, h);

    const input = summary.total_input_tokens || 0;
    const output = summary.total_output_tokens || 0;
    const cache = summary.total_cache_tokens || 0;
    const total = input + output + cache;

    if (total === 0) {
      ctx.fillStyle = '#5a6577';
      ctx.font = '14px -apple-system, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('No token data yet', centerX, centerY);
      return;
    }

    const segments = [
      { label: 'Input', value: input, color: '#3b82f6' },
      { label: 'Output', value: output, color: '#7c3aed' },
      { label: 'Cache', value: cache, color: '#22c55e' },
    ].filter(s => s.value > 0);

    let startAngle = -Math.PI / 2;
    segments.forEach(seg => {
      const sliceAngle = (seg.value / total) * 2 * Math.PI;
      ctx.beginPath();
      ctx.moveTo(centerX, centerY);
      ctx.arc(centerX, centerY, radius, startAngle, startAngle + sliceAngle);
      ctx.closePath();
      ctx.fillStyle = seg.color;
      ctx.fill();
      startAngle += sliceAngle;
    });

    // Donut hole
    ctx.beginPath();
    ctx.arc(centerX, centerY, radius * 0.6, 0, 2 * Math.PI);
    ctx.fillStyle = '#16213e';
    ctx.fill();

    // Center text
    ctx.fillStyle = '#e0e0e0';
    ctx.font = 'bold 16px -apple-system, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(this._formatNumber(total), centerX, centerY - 4);
    ctx.font = '11px -apple-system, sans-serif';
    ctx.fillStyle = '#8892a4';
    ctx.fillText('total tokens', centerX, centerY + 14);

    // Legend
    const legendY = h - 20;
    let legendX = centerX - segments.length * 50;
    segments.forEach(seg => {
      ctx.fillStyle = seg.color;
      ctx.fillRect(legendX, legendY - 8, 10, 10);
      ctx.fillStyle = '#8892a4';
      ctx.textAlign = 'left';
      ctx.font = '11px -apple-system, sans-serif';
      ctx.fillText(`${seg.label} (${Math.round(seg.value / total * 100)}%)`, legendX + 14, legendY);
      legendX += 100;
    });
  },

  _formatNumber(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return n.toString();
  },
};
