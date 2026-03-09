/* ========================================================================
   NowDJ — Package Builder
   ======================================================================== */

// pricingType values: "fixed" | "hourly" | "daily" | "tbc"

const state = {
  // id → { id, name, basePrice, price, pricingType, qty }
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
    if (item.pricingType === 'tbc') {
      priceLabel = `<span class="pkg-tbc-badge">TBC</span>`;
    } else if (item.pricingType === 'hourly' || item.pricingType === 'daily') {
      priceLabel = `${fmt(item.price)} <span style="font-size:0.72rem;color:var(--text-muted);font-weight:500">(${item.qty} ${unitLabel(item.pricingType, item.qty)})</span>`;
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
}

/* ── Quantity update (hourly / daily) ── */

function updateQty(id, delta) {
  const item = state.selected.get(id);
  if (!item || (item.pricingType !== 'hourly' && item.pricingType !== 'daily')) return;
  const newQty = Math.max(1, item.qty + delta);
  item.qty   = newQty;
  item.price = item.basePrice * newQty;

  const card = document.querySelector(`.item-card[data-id="${id}"]`);
  if (card) {
    const display = card.querySelector('.qty-display');
    if (display) display.textContent = `${newQty} ${unitLabel(item.pricingType, newQty)}`;
  }

  renderPackage();
}

/* ── Select / deselect ── */

function selectItem(id, name, basePrice, pricingType) {
  const qty   = 1;
  const price = pricingType === 'tbc' ? 0 : basePrice * qty;
  state.selected.set(id, { id, name, basePrice, price, pricingType, qty });

  const card = document.querySelector(`.item-card[data-id="${id}"]`);
  if (card) {
    card.classList.add('selected');
    card.setAttribute('aria-checked', 'true');
    if (pricingType === 'hourly' || pricingType === 'daily') {
      card.querySelector('.qty-stepper')?.style.setProperty('display', 'flex');
    }
  }
  renderPackage();
  updateSectionStates();
}

function deselectItem(id) {
  const item = state.selected.get(id);
  state.selected.delete(id);

  const card = document.querySelector(`.item-card[data-id="${id}"]`);
  if (card) {
    card.classList.remove('selected');
    card.setAttribute('aria-checked', 'false');
    const stepper = card.querySelector('.qty-stepper');
    if (stepper) stepper.style.display = 'none';
    const display = card.querySelector('.qty-display');
    if (display && item) display.textContent = `1 ${unitLabel(item.pricingType, 1)}`;
  }
  renderPackage();
  updateSectionStates();
}

function toggleItem(id, name, basePrice, pricingType) {
  if (state.selected.has(id)) {
    deselectItem(id);
  } else {
    selectItem(id, name, basePrice, pricingType);
  }
}

/* ── Wire up cards ── */

function initCards() {
  document.querySelectorAll('.item-card').forEach(card => {
    const id          = card.dataset.id;
    const name        = card.dataset.name;
    const basePrice   = parseFloat(card.dataset.price);
    const pricingType = card.dataset.pricingType || 'fixed';

    const priceEl = card.querySelector('.card-price');

    // Update price display label
    if (pricingType === 'tbc') {
      if (priceEl) { priceEl.textContent = 'TBC'; priceEl.classList.add('tbc'); }
    } else if (pricingType === 'hourly') {
      if (priceEl) priceEl.textContent = `£${basePrice}/hr`;
    } else if (pricingType === 'daily') {
      if (priceEl) priceEl.textContent = `£${basePrice}/day`;
    }

    // Inject quantity stepper for hourly / daily
    if (pricingType === 'hourly' || pricingType === 'daily') {
      const defaultLabel = `1 ${unitLabel(pricingType, 1)}`;
      const stepper = document.createElement('div');
      stepper.className = 'qty-stepper';
      stepper.style.display = 'none';
      stepper.innerHTML = `
        <button class="hours-btn" data-action="dec" aria-label="Less">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/></svg>
        </button>
        <span class="qty-display">${defaultLabel}</span>
        <button class="hours-btn" data-action="inc" aria-label="More">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        </button>`;

      stepper.querySelector('[data-action="dec"]').addEventListener('click', (e) => {
        e.stopPropagation(); updateQty(id, -1);
      });
      stepper.querySelector('[data-action="inc"]').addEventListener('click', (e) => {
        e.stopPropagation(); updateQty(id, +1);
      });

      card.appendChild(stepper);
    }

    card.setAttribute('tabindex', '0');
    card.setAttribute('role', 'checkbox');
    card.setAttribute('aria-checked', 'false');

    card.addEventListener('click', () => toggleItem(id, name, basePrice, pricingType));
    card.addEventListener('keydown', (e) => {
      if (e.key === ' ' || e.key === 'Enter') {
        e.preventDefault();
        toggleItem(id, name, basePrice, pricingType);
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
    const stepper = c.querySelector('.qty-stepper');
    if (stepper) stepper.style.display = 'none';
    const display = c.querySelector('.qty-display');
    if (display) {
      const pt = c.dataset.pricingType;
      display.textContent = `1 ${unitLabel(pt, 1)}`;
    }
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

  // Build item_quantities map for hourly/daily items
  const item_quantities = {};
  state.selected.forEach((item, id) => {
    if (item.pricingType === 'hourly' || item.pricingType === 'daily') {
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
