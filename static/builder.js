/* ========================================================================
   NowDJ — Package Builder
   ======================================================================== */

// pricingType values: "fixed" | "hourly" | "daily" | "tbc"

const state = {
  // id → { id, name, basePrice, price, pricingType, qty, days, allowQty }
  // qty  = number of units (used when allowQty or fixed)
  // days = number of hours/days (used when pricingType hourly/daily)
  // price = basePrice × qty × days
  selected: new Map(),
};

const fmt = (n) => '£' + n.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',');

function animateValue(el, newText) {
  el.classList.remove('bump');
  void el.offsetWidth;
  el.textContent = newText;
  el.classList.add('bump');
  setTimeout(() => el.classList.remove('bump'), 300);
}

function unitLabel(pt, qty) {
  if (pt === 'hourly') return qty === 1 ? 'hr'  : 'hrs';
  if (pt === 'daily')  return qty === 1 ? 'day' : 'days';
  return '';
}

/* ── Section indicators ── */

function updateSectionStates() {
  document.querySelectorAll('.service-section').forEach(section => {
    const count = section.querySelectorAll('.item-card.selected').length;
    const badge = section.querySelector('.section-count');
    if (badge) {
      badge.textContent = count;
      badge.classList.toggle('visible', count > 0);
    }
    section.classList.toggle('has-selection', count > 0);
  });
}

/* ── Package panel ── */

function renderPackage() {
  const empty    = document.getElementById('package-empty');
  const itemList = document.getElementById('package-items');
  const summary  = document.getElementById('pkg-summary');
  const totalEl  = document.getElementById('pkg-total');
  const countEl  = document.getElementById('panel-count');

  const items = [...state.selected.values()];
  const total = items.reduce((s, i) => s + i.price, 0);
  const hasTBC = items.some(i => i.pricingType === 'tbc');

  countEl.textContent = items.length;

  if (items.length === 0) {
    empty.style.display    = '';
    itemList.style.display = 'none';
    summary.style.display  = 'none';
    animateValue(totalEl, '£0');
    return;
  }

  empty.style.display    = 'none';
  itemList.style.display = 'flex';
  summary.style.display  = '';

  itemList.innerHTML = '';
  items.forEach(item => {
    let priceLabel;
    const isTimeBased = item.pricingType === 'hourly' || item.pricingType === 'daily';
    if (item.pricingType === 'tbc') {
      priceLabel = `<span class="pkg-tbc-badge">TBC</span>`;
    } else if (isTimeBased && item.allowQty) {
      // e.g. "£300 (×2, 3 days)"
      const unitPart = `${item.days} ${unitLabel(item.pricingType, item.days)}`;
      priceLabel = `${fmt(item.price)} <span style="font-size:0.72rem;color:var(--text-muted);font-weight:500">(×${item.qty}, ${unitPart})</span>`;
    } else if (isTimeBased) {
      priceLabel = `${fmt(item.price)} <span style="font-size:0.72rem;color:var(--text-muted);font-weight:500">(${item.days} ${unitLabel(item.pricingType, item.days)})</span>`;
    } else if (item.qty > 1) {
      priceLabel = `${fmt(item.price)} <span style="font-size:0.72rem;color:var(--text-muted);font-weight:500">(×${item.qty})</span>`;
    } else {
      priceLabel = fmt(item.price);
    }

    const div = document.createElement('div');
    div.className = 'pkg-item';
    div.innerHTML = `
      <span class="pkg-item-name">${item.name}</span>
      <span class="pkg-item-price">${priceLabel}</span>
      <button class="pkg-item-remove" data-remove="${item.id}" aria-label="Remove ${item.name}">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>`;
    itemList.appendChild(div);
  });

  itemList.querySelectorAll('[data-remove]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      deselectItem(btn.dataset.remove);
    });
  });

  const totalDisplay = hasTBC ? `${fmt(total)} + TBC` : fmt(total);
  animateValue(totalEl, totalDisplay);
  updateMobileBar();
}

/* ── Quantity update (hourly / daily) ── */

function qtyLabel(pricingType, qty, allowQty) {
  if (pricingType === 'hourly' || pricingType === 'daily') return `${qty} ${unitLabel(pricingType, qty)}`;
  return `×${qty}`;
}

// field: 'qty' (units) or 'days' (hours/days)
function updateQty(id, delta, field) {
  const item = state.selected.get(id);
  if (!item) return;
  const isTimeBased = item.pricingType === 'hourly' || item.pricingType === 'daily';

  if (field === 'days' && isTimeBased) {
    item.days = Math.max(1, item.days + delta);
  } else if (field === 'qty') {
    item.qty = Math.max(1, item.qty + delta);
  } else {
    // Legacy single-stepper: time-based → days, fixed+allowQty → qty
    if (isTimeBased) item.days = Math.max(1, item.days + delta);
    else item.qty = Math.max(1, item.qty + delta);
  }

  item.price = item.pricingType === 'tbc' ? 0 : item.basePrice * item.qty * item.days;

  const card = document.querySelector(`.item-card[data-id="${id}"]`);
  if (card) {
    const daysDisplay = card.querySelector('.qty-display-days');
    const qtyDisplay  = card.querySelector('.qty-display-qty');
    const display     = card.querySelector('.qty-display'); // legacy single stepper
    if (daysDisplay) daysDisplay.textContent = `${item.days} ${unitLabel(item.pricingType, item.days)}`;
    if (qtyDisplay)  qtyDisplay.textContent  = `×${item.qty}`;
    if (display)     display.textContent     = isTimeBased
      ? `${item.days} ${unitLabel(item.pricingType, item.days)}`
      : `×${item.qty}`;
  }

  renderPackage();
}

/* ── Select / deselect ── */

function selectItem(id, name, basePrice, pricingType, allowQty) {
  const qty  = 1;
  const days = 1;
  const price = pricingType === 'tbc' ? 0 : basePrice * qty * days;
  state.selected.set(id, { id, name, basePrice, price, pricingType, qty, days, allowQty: !!allowQty });

  const card = document.querySelector(`.item-card[data-id="${id}"]`);
  if (card) {
    card.classList.add('selected');
    card.setAttribute('aria-checked', 'true');
    // Show pair wrapper or individual steppers
    const pair = card.querySelector('.qty-stepper-pair');
    if (pair) pair.style.display = 'flex';
    else card.querySelectorAll('.qty-stepper').forEach(s => s.style.display = 'flex');
  }
  renderPackage();
  updateSectionStates();
}

function deselectItem(id) {
  state.selected.delete(id);

  const card = document.querySelector(`.item-card[data-id="${id}"]`);
  if (card) {
    card.classList.remove('selected');
    card.setAttribute('aria-checked', 'false');
    const pair = card.querySelector('.qty-stepper-pair');
    if (pair) pair.style.display = 'none';
    else card.querySelectorAll('.qty-stepper').forEach(s => s.style.display = 'none');
    const pt = card.dataset.pricingType || 'fixed';
    const daysDisplay = card.querySelector('.qty-display-days');
    const qtyDisplay  = card.querySelector('.qty-display-qty');
    const display     = card.querySelector('.qty-display');
    if (daysDisplay) daysDisplay.textContent = `1 ${unitLabel(pt, 1)}`;
    if (qtyDisplay)  qtyDisplay.textContent  = '×1';
    if (display)     display.textContent     = qtyLabel(pt, 1, card.dataset.allowQuantity === 'true');
  }
  renderPackage();
  updateSectionStates();
}

function toggleItem(id, name, basePrice, pricingType, allowQty) {
  if (state.selected.has(id)) {
    deselectItem(id);
  } else {
    selectItem(id, name, basePrice, pricingType, allowQty);
  }
}

/* ── Wire up cards ── */

function initCards() {
  document.querySelectorAll('.item-card').forEach(card => {
    const id          = card.dataset.id;
    const name        = card.dataset.name;
    const basePrice   = parseFloat(card.dataset.price);
    const pricingType = card.dataset.pricingType || 'fixed';
    const allowQty    = card.dataset.allowQuantity === 'true';

    const priceEl = card.querySelector('.card-price');

    // Update price display label
    if (pricingType === 'tbc') {
      if (priceEl) { priceEl.textContent = 'TBC'; priceEl.classList.add('tbc'); }
    } else if (pricingType === 'hourly') {
      if (priceEl) priceEl.textContent = `£${basePrice}/hr`;
    } else if (pricingType === 'daily') {
      if (priceEl) priceEl.textContent = `£${basePrice}/day`;
    }

    // Inject quantity stepper(s) for hourly / daily / allow_quantity items
    const isTimeBased = pricingType === 'hourly' || pricingType === 'daily';
    const makeStepper = (labelHtml, decCb, incCb) => {
      const s = document.createElement('div');
      s.className = 'qty-stepper';
      s.style.display = 'none';
      s.innerHTML = `
        <button class="hours-btn" data-action="dec" aria-label="Less">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/></svg>
        </button>
        ${labelHtml}
        <button class="hours-btn" data-action="inc" aria-label="More">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        </button>`;
      s.querySelector('[data-action="dec"]').addEventListener('click', (e) => { e.stopPropagation(); decCb(); });
      s.querySelector('[data-action="inc"]').addEventListener('click', (e) => { e.stopPropagation(); incCb(); });
      return s;
    };

    if (isTimeBased && allowQty) {
      // Two steppers side by side: qty (units) + days/hours
      const qtyS  = makeStepper(`<span class="qty-display-qty">×1</span>`,
        () => updateQty(id, -1, 'qty'), () => updateQty(id, +1, 'qty'));
      const daysS = makeStepper(`<span class="qty-display-days">1 ${unitLabel(pricingType, 1)}</span>`,
        () => updateQty(id, -1, 'days'), () => updateQty(id, +1, 'days'));
      const wrapper = document.createElement('div');
      wrapper.className = 'qty-stepper-pair';
      wrapper.style.display = 'none';
      wrapper.appendChild(qtyS);
      wrapper.appendChild(daysS);
      // Show/hide wrapper instead of individual steppers
      qtyS.style.display  = 'flex';
      daysS.style.display = 'flex';
      card.appendChild(wrapper);
    } else if (isTimeBased) {
      // Single days stepper for daily/hourly without qty
      const s = makeStepper(`<span class="qty-display">1 ${unitLabel(pricingType, 1)}</span>`,
        () => updateQty(id, -1, null), () => updateQty(id, +1, null));
      card.appendChild(s);
    } else if (allowQty) {
      // Fixed-price with allow_quantity: single qty stepper
      const s = makeStepper(`<span class="qty-display">×1</span>`,
        () => updateQty(id, -1, null), () => updateQty(id, +1, null));
      card.appendChild(s);
    }

    card.setAttribute('tabindex', '0');
    card.setAttribute('role', 'checkbox');
    card.setAttribute('aria-checked', 'false');

    card.addEventListener('click', () => toggleItem(id, name, basePrice, pricingType, allowQty));
    card.addEventListener('keydown', (e) => {
      if (e.key === ' ' || e.key === 'Enter') {
        e.preventDefault();
        toggleItem(id, name, basePrice, pricingType, allowQty);
      }
    });
  });
}

/* ── Form ── */

function validateForm() {
  let valid = true;
  ['f-name', 'f-email'].forEach(fid => {
    const el = document.getElementById(fid);
    if (!el) return;
    if (!el.value.trim()) { el.classList.add('invalid'); valid = false; }
    else el.classList.remove('invalid');
  });
  const emailEl = document.getElementById('f-email');
  if (emailEl && emailEl.value.trim() && !emailEl.value.includes('@')) {
    emailEl.classList.add('invalid');
    valid = false;
  }
  return valid;
}

function showError(msg) {
  const el = document.getElementById('form-error');
  el.textContent = msg;
  el.classList.add('visible');
}

function clearError() {
  document.getElementById('form-error')?.classList.remove('visible');
}

function showSuccess(quoteId, total) {
  document.getElementById('form-content').style.display = 'none';
  const overlay = document.getElementById('success-overlay');
  overlay.classList.add('visible');
  document.getElementById('success-quote-num').textContent = `Quote #${quoteId}`;
  document.getElementById('success-total').textContent = fmt(total);
}

function resetAll() {
  state.selected.clear();
  document.querySelectorAll('.item-card.selected').forEach(c => {
    c.classList.remove('selected');
    c.setAttribute('aria-checked', 'false');
    const pair = c.querySelector('.qty-stepper-pair');
    if (pair) pair.style.display = 'none';
    else c.querySelectorAll('.qty-stepper').forEach(s => s.style.display = 'none');
    const pt = c.dataset.pricingType || 'fixed';
    const aq = c.dataset.allowQuantity === 'true';
    const d = c.querySelector('.qty-display');
    if (d) d.textContent = qtyLabel(pt, 1, aq);
    const dd = c.querySelector('.qty-display-days');
    if (dd) dd.textContent = `1 ${unitLabel(pt, 1)}`;
    const dq = c.querySelector('.qty-display-qty');
    if (dq) dq.textContent = '×1';
  });
  renderPackage();
  updateSectionStates();
  document.getElementById('quote-form')?.reset();
  document.querySelectorAll('.invalid').forEach(el => el.classList.remove('invalid'));
  clearError();
  document.getElementById('success-overlay')?.classList.remove('visible');
  document.getElementById('form-content').style.display = '';
}

async function handleSubmit(e) {
  e.preventDefault();
  clearError();

  if (state.selected.size === 0) {
    showError('Please select at least one item before requesting a quote.');
    document.querySelector('.services-column')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    return;
  }

  if (!validateForm()) {
    showError('Please fill in your name and a valid email address.');
    return;
  }

  const btn = document.getElementById('btn-submit');
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Sending…`;

  // Build item_quantities map: for time-based items store days; for allowQty store qty
  // For items with both (daily+allowQty), encode as "qty:days"
  const item_quantities = {};
  state.selected.forEach((item, id) => {
    const isTimeBased = item.pricingType === 'hourly' || item.pricingType === 'daily';
    if (isTimeBased && item.allowQty) {
      item_quantities[id] = `${item.qty}:${item.days}`;
    } else if (isTimeBased) {
      item_quantities[id] = item.days;
    } else if (item.allowQty && item.qty > 1) {
      item_quantities[id] = item.qty;
    }
  });

  const payload = {
    name:            document.getElementById('f-name').value.trim(),
    email:           document.getElementById('f-email').value.trim(),
    phone:           document.getElementById('f-phone').value.trim(),
    event_date:      document.getElementById('f-date').value,
    location:        document.getElementById('f-location').value.trim(),
    event_type:      document.getElementById('f-event-type').value,
    selected_items:  [...state.selected.keys()],
    item_quantities,
    message:         document.getElementById('f-message').value.trim(),
  };

  try {
    const res  = await fetch('/submit-quote', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (!res.ok) {
      showError(data.detail || 'Something went wrong. Please try again.');
      btn.disabled = false;
      btn.innerHTML = sendBtnHTML();
      return;
    }

    showSuccess(data.quote_id, data.total_price);
  } catch {
    showError('Network error. Please check your connection and try again.');
    btn.disabled = false;
    btn.innerHTML = sendBtnHTML();
  }
}

function sendBtnHTML() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg> Request Quote`;
}

/* ── Mobile basket bar + sheet ── */

const mobileBar     = document.getElementById('mobile-basket-bar');
const mobileSheet   = document.getElementById('mobile-sheet');
const sheetOverlay  = document.getElementById('mobile-sheet-overlay');

function updateMobileBar() {
  if (!mobileBar) return;
  const count = state.selected.size;
  const total = [...state.selected.values()].reduce((s, i) => s + (i.pricingType === 'tbc' ? 0 : i.price), 0);

  if (count > 0) {
    document.getElementById('mbb-count').textContent = `${count} item${count !== 1 ? 's' : ''}`;
    document.getElementById('mbb-total').textContent = fmt(total);
    mobileBar.classList.add('visible');
    mobileBar.setAttribute('aria-hidden', 'false');
    document.body.classList.add('basket-bar-visible');
  } else {
    mobileBar.classList.remove('visible');
    mobileBar.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('basket-bar-visible');
    closeMobileSheet();
  }

  // Keep sheet in sync if open
  if (mobileSheet?.classList.contains('open')) renderMobileSheet();
}

function renderMobileSheet() {
  const container = document.getElementById('mobile-sheet-items');
  const totalEl   = document.getElementById('mobile-sheet-total');
  if (!container) return;

  const items = [...state.selected.values()];
  if (!items.length) {
    container.innerHTML = '<div class="mobile-sheet-empty">Nothing selected yet.</div>';
    if (totalEl) totalEl.textContent = '£0';
    return;
  }

  let total = 0;
  container.innerHTML = '';
  items.forEach(item => {
    const price = item.pricingType === 'tbc' ? 0 : item.price;
    total += price;
    const qtyNote = (item.pricingType === 'hourly' || item.pricingType === 'daily')
      ? ` <span style="font-size:0.75rem;color:var(--text-muted)">(${item.qty} ${unitLabel(item.pricingType, item.qty)})</span>`
      : (item.allowQty && item.qty > 1 ? ` <span style="font-size:0.75rem;color:var(--text-muted)">(×${item.qty})</span>` : '');
    const priceStr = item.pricingType === 'tbc' ? 'TBC' : fmt(price);

    const div = document.createElement('div');
    div.className = 'mobile-sheet-item';
    div.innerHTML = `
      <span class="mobile-sheet-item-name">${item.name}${qtyNote}</span>
      <span class="mobile-sheet-item-price">${priceStr}</span>
      <button class="mobile-sheet-item-remove" data-remove="${item.id}" aria-label="Remove">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>`;
    div.querySelector('[data-remove]').addEventListener('click', (e) => {
      e.stopPropagation();
      deselectItem(item.id);
    });
    container.appendChild(div);
  });

  if (totalEl) totalEl.textContent = fmt(total);
}

function openMobileSheet() {
  if (!mobileSheet) return;
  renderMobileSheet();
  mobileSheet.classList.add('open');
  sheetOverlay?.classList.add('open');
  mobileSheet.setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';
}

function closeMobileSheet() {
  if (!mobileSheet) return;
  mobileSheet.classList.remove('open');
  sheetOverlay?.classList.remove('open');
  mobileSheet.setAttribute('aria-hidden', 'true');
  document.body.style.overflow = '';
}

document.getElementById('mbb-open-btn')?.addEventListener('click', openMobileSheet);
document.getElementById('mbb-close-btn')?.addEventListener('click', closeMobileSheet);
sheetOverlay?.addEventListener('click', closeMobileSheet);

document.getElementById('mbb-goto-form')?.addEventListener('click', () => {
  closeMobileSheet();
  setTimeout(() => {
    document.querySelector('.quote-form-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, 320);
});

/* ── Init ── */

document.addEventListener('DOMContentLoaded', () => {
  initCards();
  renderPackage();

  ['f-name', 'f-email'].forEach(id => {
    document.getElementById(id)?.addEventListener('input', (e) => e.target.classList.remove('invalid'));
  });

  document.getElementById('quote-form')?.addEventListener('submit', handleSubmit);
  document.getElementById('btn-new-quote')?.addEventListener('click', resetAll);
});
