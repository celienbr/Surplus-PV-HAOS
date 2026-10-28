"use strict";
(function(){
 const icons={
 house:'<circle cx="53" cy="12" r="8" fill="#ffc83d" stroke="#b07900"/><path d="M53 1v3m0 16v3M42 12h-3m25 0h-3M45 4l-2-2m18 19-2-2" stroke="#c38b00"/><path d="M7 34 29 12l24 22M12 31v25h36V31"/><path d="M25 56V41h10v15"/><path d="m24 18 17 0 11 17H35z" fill="#1179c6" stroke="#cfeeff"/><path d="m30 18 11 17m-5-17 11 17M28 24h17m-13 6h17" stroke="#e2f6ff"/><path d="M2 57h60" stroke="#3d8e51"/><path d="M8 55C-2 44 9 37 12 50m41 5c-6-17 12-17 8-3" fill="#5caa60" stroke="#367c46"/>',
 grid:'<path d="M32 5 16 58m16-53 16 53M32 5v10M23 14h18L50 26H14zM21 35h22M18 47h28M14 58h36M24 26l18 21m-2-21L22 47m1-12 23 23m-5-23L18 58"/>',
 solar:'<circle cx="19" cy="18" r="9" fill="#ffd34f" stroke="#ae7c00"/><path d="M19 3v3m0 24v3M4 18h3m25 0h3M8 7l3 3m16 16 3 3M8 29l3-3M28 8l-3 3" stroke="#ba8200"/><path d="m32 29 24 0-6 22H26z" fill="#2687cd" stroke="#def1ff"/><path d="m40 29-6 22m14-22-6 22m-12-15h24m-26 8h24" stroke="#d9f4ff"/><path d="M39 51v9m-10 0h20"/>',
 weather:'<path d="M12 42c-14-2-9-20 3-18 1-19 27-21 30-3 16-1 18 21 3 21z" fill="#66baf0" stroke="#2073a9"/><path d="M42 27v21a8 8 0 1 0 10 0V27a5 5 0 0 0-10 0z" fill="var(--panel-bg)"/><path d="M47 32v22" stroke="#d43856" stroke-width="4"/><circle cx="47" cy="54" r="3" fill="#d43856" stroke="#d43856"/>',
 settings:'<path d="M8 16h48M8 32h48M8 48h48"/><circle cx="23" cy="16" r="6" fill="var(--panel-bg)"/><circle cx="43" cy="32" r="6" fill="var(--panel-bg)"/><circle cx="19" cy="48" r="6" fill="var(--panel-bg)"/>',
 pac:'<rect x="5" y="10" width="54" height="43" rx="5"/><circle cx="27" cy="31" r="15"/><circle cx="27" cy="31" r="3" fill="var(--pac-chart)"/><path d="M27 28c-17-14-17 9-5 7m8-4c16-15-8-18-6-7m3 10c-1 20 19 7 8-2" stroke="var(--pac-chart)"/><path d="M48 21h5m-5 8h5m-5 8h5M12 54v5m40-5v5"/>',
 boiler:'<rect x="19" y="5" width="29" height="48" rx="12"/><path d="M25 53v7m17-7v7M33 18v21"/><circle cx="33" cy="40" r="5" fill="var(--temp-chart)"/><path d="M52 17h6m-6 8h4m-4 8h6"/>',
 shield:'<path d="M32 5C23 13 16 14 8 14v17c0 14 14 24 24 29 10-5 24-15 24-29V14c-8 0-15-1-24-9z" fill="var(--panel-soft)" stroke="var(--target-chart)" stroke-width="3"/><path d="m22 32 7 7 15-17" stroke="var(--target-chart)" stroke-width="4"/>',
 leaf:'<path d="M13 55C-1 25 35 5 56 5c-1 28-17 51-43 50z" fill="#398b51" stroke="#398b51"/><path d="M9 60 46 16M22 43l-2-15m12 3 15-2" stroke="#effff0"/>'};
 document.querySelectorAll('[data-icon]').forEach(el=>{const shape=icons[el.dataset.icon];if(shape)el.innerHTML='<svg class="icon" viewBox="0 0 64 64" aria-hidden="true">'+shape+'</svg>';});
 document.querySelectorAll('.bottom a').forEach(a=>{if(a.pathname===location.pathname&&(!a.hash||a.hash===location.hash))a.setAttribute('aria-current','page');});
})();
