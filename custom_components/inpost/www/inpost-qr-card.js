/*
 * inpost-qr-card - karta Lovelace integracji InPost (paczki).
 *
 * Pokazuje paczki czekajace w paczkomacie z kodem QR do zeskanowania (tap = pelny ekran),
 * adresem, terminem odbioru i - gdy w opcjach integracji wlaczono publikowanie kodow -
 * kodem odbioru. Pod spodem lista paczek w drodze.
 *
 * Karta niczego nie konfiguruje recznie: sama znajduje encje integracji po atrybucie
 * `inpost_type`. Obrazek QR idzie przez uwierzytelnione /api/image_proxy.
 *
 * type: custom:inpost-qr-card
 * title: Paczki            # opcjonalnie
 * account: "123456789"     # opcjonalnie - tylko jedno konto
 * show_code: true          # kod odbioru tekstem (wymaga opcji "Publikuj kody odbioru")
 * show_in_transit: true    # lista paczek w drodze
 * show_delivered: false    # odebrane w ostatnich 12 h
 * qr_size: 150             # bok QR w px
 */
const DONE = ["DELIVERED", "PICKED_UP"];

function esc(v) {
  return String(v == null ? "" : v).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// "zostalo 1 d 4 h" / "zostalo 3 h 20 min" / "termin minal"; pilne = mniej niz 12 h
function deadlineText(iso, now) {
  const t = Date.parse(iso || "");
  if (isNaN(t)) return null;
  const min = Math.floor((t - now) / 60000);
  if (min <= 0) return { text: "termin minął", urgent: true };
  const d = Math.floor(min / 1440), h = Math.floor((min % 1440) / 60), m = min % 60;
  const text = d > 0 ? `zostało ${d} d ${h} h` : h > 0 ? `zostało ${h} h ${m} min` : `zostało ${m} min`;
  return { text, urgent: min < 720 };
}

function deadlineDate(iso) {
  const t = Date.parse(iso || "");
  if (isNaN(t)) return "";
  return new Date(t).toLocaleString("pl-PL", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

// Wybiera z hass.states encje integracji i dzieli je na: do odbioru / w drodze / odebrane.
function collect(states, config) {
  const account = config.account ? String(config.account).replace(/\D/g, "").slice(-9) : "";
  const mine = (s) => !account || String(s.attributes.account || "").slice(-9) === account;
  const alive = (s) => s.state !== "unavailable" && s.state !== "unknown";
  const qrBySn = {};
  const parcels = [];
  for (const id of Object.keys(states)) {
    const s = states[id];
    const type = s.attributes && s.attributes.inpost_type;
    if (!type || !mine(s) || !alive(s)) continue;
    if (type === "qr" && id.startsWith("image.")) qrBySn[s.attributes.shipment_number] = s;
    else if (type === "parcel" && id.startsWith("sensor.")) parcels.push(s);
  }
  const ready = [], transit = [], delivered = [];
  const seen = new Set();
  for (const p of parcels) {
    const a = p.attributes;
    seen.add(a.shipment_number);
    if (a.in_locker) ready.push({ parcel: p, qr: qrBySn[a.shipment_number] || null, a });
    else if (DONE.includes(a.status_raw)) delivered.push(p);
    else transit.push(p);
  }
  // QR bez encji paczki (np. encja paczki wylaczona recznie) - i tak pokaz
  for (const sn of Object.keys(qrBySn)) {
    if (!seen.has(sn)) ready.push({ parcel: null, qr: qrBySn[sn], a: qrBySn[sn].attributes });
  }
  const key = (x) => Date.parse(x.a.expiry_date || "") || Infinity;
  ready.sort((x, y) => key(x) - key(y));
  return { ready, transit, delivered };
}

class InpostQrCard extends HTMLElement {
  static getStubConfig() {
    return { title: "Paczki InPost", show_code: true, show_in_transit: true };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        { name: "account", selector: { text: {} } },
        { name: "show_code", selector: { boolean: {} } },
        { name: "show_in_transit", selector: { boolean: {} } },
        { name: "show_delivered", selector: { boolean: {} } },
        { name: "qr_size", selector: { number: { min: 80, max: 400, step: 10, mode: "box" } } },
      ],
      computeLabel: (s) => ({
        title: "Tytuł",
        account: "Tylko konto (numer telefonu, puste = wszystkie)",
        show_code: "Pokaż kod odbioru tekstem",
        show_in_transit: "Pokaż paczki w drodze",
        show_delivered: "Pokaż odebrane (ostatnie 12 h)",
        qr_size: "Rozmiar kodu QR (px)",
      }[s.name]),
    };
  }

  setConfig(config) {
    this._config = { show_code: true, show_in_transit: true, show_delivered: false, qr_size: 150, ...config };
    this._sig = null;
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 3 + 3 * (this._readyCount || 0);
  }

  connectedCallback() {
    // odliczanie do terminu odswiezamy co minute, nawet gdy stany sie nie zmieniaja
    this._timer = setInterval(() => { this._sig = null; this._render(); }, 60000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._closeOverlay();
  }

  _render() {
    if (!this._hass || !this._config) return;
    const cfg = this._config;
    const data = collect(this._hass.states, cfg);
    this._readyCount = data.ready.length;
    // przerysuj tylko gdy zmienilo sie cos, co pokazujemy
    const sig = JSON.stringify([
      cfg,
      data.ready.map((r) => [r.a.shipment_number, r.qr && r.qr.attributes.entity_picture, r.a.open_code, r.a.expiry_date,
        r.parcel && r.parcel.state, r.parcel && r.parcel.attributes.friendly_name]),
      data.transit.map((p) => [p.entity_id, p.state, p.attributes.friendly_name]),
      data.delivered.map((p) => [p.entity_id, p.state]),
    ]);
    if (sig === this._sig) return;
    this._sig = sig;

    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const now = Date.now();
    const size = Math.max(80, Number(cfg.qr_size) || 150);

    const tiles = data.ready.map((r, i) => {
      const a = r.a;
      const sender = a.sender || (r.parcel && r.parcel.attributes.friendly_name) || "Paczka";
      const dl = deadlineText(a.expiry_date, now);
      const code = cfg.show_code && a.open_code ? `<div class="code">${esc(a.open_code)}</div>` : "";
      const qr = r.qr && r.qr.attributes.entity_picture
        ? `<img class="qr" data-i="${i}" width="${size}" height="${size}" alt="Kod QR" src="${esc(r.qr.attributes.entity_picture)}">`
        : `<div class="noqr" style="width:${size}px;height:${size}px">brak kodu QR</div>`;
      return `<div class="tile">
        ${qr}
        <div class="info">
          <div class="sender">${esc(sender)}</div>
          <div class="addr">${esc(a.address || "")}</div>
          <div class="sub">${esc(a.pickup_point || "")}${a.location_description ? " · " + esc(a.location_description) : ""}</div>
          ${code}
          ${dl ? `<div class="deadline${dl.urgent ? " urgent" : ""}">${esc(dl.text)} <span class="sub">(do ${esc(deadlineDate(a.expiry_date))})</span></div>` : ""}
        </div>
      </div>`;
    }).join("");

    const row = (p, dim) => `<div class="row${dim ? " dim" : ""}" data-entity="${esc(p.entity_id)}">
        <span class="rname">${esc(p.attributes.sender || p.attributes.friendly_name || "Paczka")}</span>
        <span class="rstate">${esc(p.state)}</span>
      </div>`;
    const transit = cfg.show_in_transit && data.transit.length
      ? `<div class="section">W drodze (${data.transit.length})</div>${data.transit.map((p) => row(p, false)).join("")}` : "";
    const delivered = cfg.show_delivered && data.delivered.length
      ? `<div class="section">Odebrane</div>${data.delivered.map((p) => row(p, true)).join("")}` : "";
    const empty = !data.ready.length
      ? `<div class="empty">Brak paczek do odbioru</div>` : "";

    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 12px 16px 16px; }
        .head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
        .title { font-size: 1.2em; font-weight: 500; }
        .count { color: var(--secondary-text-color); }
        .tile { display: flex; gap: 14px; align-items: center; padding: 10px 0; border-top: 1px solid var(--divider-color); flex-wrap: wrap; }
        .tile:first-of-type { border-top: none; }
        .qr { background: #fff; border-radius: 8px; padding: 4px; cursor: zoom-in; box-sizing: border-box; image-rendering: pixelated; }
        .noqr { display: flex; align-items: center; justify-content: center; border: 1px dashed var(--divider-color); border-radius: 8px; color: var(--secondary-text-color); font-size: .85em; }
        .info { flex: 1; min-width: 150px; }
        .sender { font-weight: 500; font-size: 1.05em; }
        .addr { margin-top: 2px; }
        .sub { color: var(--secondary-text-color); font-size: .85em; }
        .code { font-family: var(--code-font-family, monospace); font-size: 1.8em; letter-spacing: .15em; margin-top: 6px; }
        .deadline { margin-top: 6px; }
        .deadline.urgent { color: var(--error-color); font-weight: 500; }
        .section { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--divider-color); color: var(--secondary-text-color); font-size: .85em; text-transform: uppercase; letter-spacing: .05em; }
        .row { display: flex; justify-content: space-between; gap: 12px; padding: 5px 0; cursor: pointer; }
        .row.dim { opacity: .55; }
        .rstate { color: var(--secondary-text-color); text-align: right; }
        .empty { color: var(--secondary-text-color); padding: 12px 0; }
      </style>
      <ha-card>
        <div class="head">
          <span class="title">${esc(cfg.title || "Paczki InPost")}</span>
          <span class="count">${data.ready.length ? `do odbioru: ${data.ready.length}` : ""}</span>
        </div>
        ${empty}${tiles}${transit}${delivered}
      </ha-card>`;

    this.shadowRoot.querySelectorAll("img.qr").forEach((img) => {
      img.addEventListener("click", () => this._openOverlay(data.ready[Number(img.dataset.i)]));
    });
    this.shadowRoot.querySelectorAll(".row").forEach((el) => {
      el.addEventListener("click", () => this.dispatchEvent(new CustomEvent("hass-more-info", {
        detail: { entityId: el.dataset.entity }, bubbles: true, composed: true,
      })));
    });
  }

  // Pelny ekran: bialy, QR na cala szerokosc - do przylozenia do skanera paczkomatu.
  _openOverlay(item) {
    if (!item || !item.qr) return;
    this._closeOverlay();
    const a = item.a;
    const o = document.createElement("div");
    o.style.cssText = "position:fixed;inset:0;z-index:99999;background:#fff;color:#000;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;padding:16px;box-sizing:border-box;cursor:zoom-out;font-family:sans-serif;text-align:center";
    const code = this._config.show_code && a.open_code
      ? `<div style="font-family:monospace;font-size:2.6em;letter-spacing:.2em">${esc(a.open_code)}</div>` : "";
    o.innerHTML = `
      <img alt="Kod QR" src="${esc(item.qr.attributes.entity_picture)}" style="width:min(88vw,70vh);height:min(88vw,70vh);image-rendering:pixelated">
      ${code}
      <div style="font-size:1.1em">${esc(a.sender || "")}</div>
      <div style="color:#555">${esc(a.address || "")} · ${esc(a.pickup_point || "")}</div>`;
    o.addEventListener("click", () => this._closeOverlay());
    document.body.appendChild(o);
    this._overlay = o;
  }

  _closeOverlay() {
    if (this._overlay) {
      this._overlay.remove();
      this._overlay = null;
    }
  }
}

if (!customElements.get("inpost-qr-card")) {
  customElements.define("inpost-qr-card", InpostQrCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "inpost-qr-card",
    name: "InPost — paczki i kody QR",
    description: "Paczki czekające w paczkomacie z kodem QR (tap = pełny ekran), terminem odbioru i listą paczek w drodze.",
    preview: true,
    documentationURL: "https://github.com/radekpskowski-ship-it/inpost-home-assistant",
  });
}

if (typeof module !== "undefined") module.exports = { esc, deadlineText, collect };
