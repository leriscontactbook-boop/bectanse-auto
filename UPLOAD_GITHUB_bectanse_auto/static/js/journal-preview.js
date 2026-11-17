/* Public, offline calendar using the member journal's native stylesheet and markup.
   All positions below are fictional demo data; no account or trading API is called. */
(() => {
  const sessions = {1:[120,-45],2:[85,60],3:[-70,-35],4:[180,-50],7:[65,95],8:[-90,40],9:[110,55],10:[-60,-40],11:[150,75],14:[80,-35],15:[-85,30],16:[140,65],17:[95,-50],18:[-110,40],21:[120,80],22:[-65,-40],23:[175,55],24:[85,-30],25:[-75,45],28:[100,60],29:[-50,90],30:[135,-45]};
  const money = n => new Intl.NumberFormat('fr-FR',{style:'currency',currency:'EUR',maximumFractionDigits:0,signDisplay:'exceptZero'}).format(n);
  const sum = a => a.reduce((s,n)=>s+n,0);
  class JournalPreview extends HTMLElement {
    connectedCallback() {
      if(this.shadowRoot) return;
      const root=this.attachShadow({mode:'open'}), trades=Object.values(sessions).flat(), wins=trades.filter(n=>n>0), losses=trades.filter(n=>n<0);
      root.innerHTML=`<link rel="stylesheet" href="/static/css/trading-journal.css"><link rel="stylesheet" href="/static/css/journal-preview.css?v=1">
        <div class="preview">
          <header class="brand"><div><span class="brand-mark">B</span><strong>Bectanse <span>Journal</span></strong></div><span class="demo">Bectanse Journal</span></header>
          <div class="navigation"><span>Vue d’ensemble</span><strong>Calendrier</strong><span>Historique</span><span>Statistiques</span></div>
          <div class="tj-calendar-head"><div><p class="kicker">Ton activité, jour après jour</p><h2>Septembre 2026</h2></div><span class="currency">EUR</span></div>
          <div class="tj-month-strip">
            <div><small>P&L du mois</small><strong class="positive">${money(sum(trades))}</strong></div>
            <div><small>Win rate</small><strong>${(wins.length/trades.length*100).toFixed(1).replace('.',',')} %</strong></div>
            <div><small>Positions</small><strong>${trades.length}</strong></div>
            <div><small>Profit factor</small><strong>${(sum(wins)/-sum(losses)).toFixed(2).replace('.',',')}</strong></div>
          </div>
          <article class="tj-calendar-shell" aria-label="Calendrier de septembre 2026">
            <div class="tj-weekdays">${['Lun','Mar','Mer','Jeu','Ven','Sam','Dim'].map(d=>`<span>${d}</span>`).join('')}</div>
            <div class="tj-calendar-grid"></div>
          </article>
          <p class="hint"><span class="dot"></span> Sélectionne un jour pour explorer tes positions</p>
          <section class="detail" aria-live="polite" aria-atomic="true"></section>
          <footer>Bectanse Journal</footer>
        </div>`;
      const grid=root.querySelector('.tj-calendar-grid');
      grid.innerHTML='<div class="tj-calendar-day is-outside" aria-hidden="true"></div>';
      for(let day=1;day<=30;day++) {
        const values=sessions[day]||[], total=sum(values), button=document.createElement('button');
        button.type='button'; button.className='tj-calendar-day '+(values.length?(total>=0?'is-positive':'is-negative'):'');
        button.setAttribute('aria-label',`${day} septembre : ${values.length?money(total)+', '+values.length+' positions':'aucune position'}`);
        button.setAttribute('aria-pressed','false');button.dataset.day=day;
        button.innerHTML=`<span>${String(day).padStart(2,'0')}</span><b data-mobile="${total>0?'+':''}${total}">${values.length?money(total):'—'}</b><small>${values.length?'2 trades':''}</small>`;
        button.addEventListener('click',()=>this.select(day));grid.appendChild(button);
      }
      for(let i=0;i<4;i++) grid.insertAdjacentHTML('beforeend','<div class="tj-calendar-day is-outside" aria-hidden="true"></div>');
      this.select(23);
    }
    select(day) {
      const root=this.shadowRoot,values=sessions[day]||[],label=new Intl.DateTimeFormat('fr-FR',{timeZone:'Europe/Paris',weekday:'long',day:'numeric',month:'long'}).format(new Date(2026,8,day));
      root.querySelectorAll('[data-day]').forEach(el=>el.setAttribute('aria-pressed',String(Number(el.dataset.day)===day)));
      root.querySelector('.detail').innerHTML=`<div class="detail-head"><strong>${label}</strong><span class="${sum(values)<0?'negative':'positive'}">${money(sum(values))}</span></div>${values.length?values.map((n,i)=>`<div class="position"><div><b>${i?'EURUSD':'XAUUSD'}</b><small>${i?'Vente · 0,10 lot':'Achat · 0,05 lot'}</small></div><span>${i?'14:35 – 15:10':'09:15 – 10:02'}</span><strong class="${n<0?'negative':'positive'}">${money(n)}</strong></div>`).join(''):'<p class="empty">Aucune position clôturée ce jour</p>'}`;
    }
  }
  customElements.define('journal-preview',JournalPreview);
})();
