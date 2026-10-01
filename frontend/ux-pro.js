(() => {
  'use strict';

  const root = document.documentElement;
  function injectProWelcome() {
    const dashboard = document.getElementById('tab-dashboard');
    if (!dashboard || document.getElementById('ema-pro-welcome')) return;
    const hero = document.createElement('div');
    hero.id = 'ema-pro-welcome';
    hero.className = 'glass-card rounded-[28px] p-5 sm:p-6 mb-1';
    hero.innerHTML = `
      <div class="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
        <div class="min-w-0">
          <div class="flex items-center gap-2 text-[10px] font-mono uppercase tracking-[.16em] text-indigo-300">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
            EMA intelligence online
          </div>
          <h3 class="mt-2 text-lg sm:text-xl font-black text-white tracking-tight">Your inbox, interpreted before you have to.</h3>
          <p id="ema-pro-welcome-copy" class="mt-1.5 text-[11px] leading-6 text-slate-400 max-w-2xl">EMA continuously classifies incoming mail, extracts time-bound work, and keeps your calendar organized.</p>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <button type="button" class="btn-action px-3 py-2.5 rounded-xl text-[11px] font-bold text-indigo-200 bg-indigo-500/10 border border-indigo-500/20" onclick="switchTab('tab-emails')">Open inbox</button>
          <button type="button" class="btn-action px-3 py-2.5 rounded-xl text-[11px] font-bold text-cyan-200 bg-cyan-500/10 border border-cyan-500/20" onclick="switchTab('tab-calendar')">Open calendar</button>
        </div>
      </div>`;
    const grid = dashboard.querySelector(':scope > div.grid.grid-cols-2');
    if (grid) dashboard.insertBefore(hero, grid);
  }

  function updateProWelcome() {
    const copy = document.getElementById('ema-pro-welcome-copy');
    if (!copy) return;
    const p = Number((document.getElementById('kpi-total')?.textContent || '0').replace(/,/g, ''));
    const a = Number((document.getElementById('kpi-actioned')?.textContent || '0').replace(/,/g, ''));
    if (p > 0) copy.textContent = `EMA has processed ${p.toLocaleString('en-IN')} email${p === 1 ? '' : 's'} and surfaced ${a.toLocaleString('en-IN')} calendar action${a === 1 ? '' : 's'}. Your timeline stays focused while EMA handles the triage.`;
  }

  function start() {
    injectProWelcome();
    // Main dashboard owns KPI animation and icon rendering; don't duplicate it here.
    window.setTimeout(updateProWelcome, 120);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
