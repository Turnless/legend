const grid = document.querySelector('#meme-grid');
const lightbox = document.querySelector('#lightbox');
const lightboxImage = lightbox.querySelector('img');
const lightboxCaption = lightbox.querySelector('figcaption');
let activeMeme = 1;

for (let i = 1; i <= 20; i++) {
  const n = String(i).padStart(2, '0');
  const card = document.createElement('button');
  card.className = 'meme-card reveal';
  card.type = 'button';
  card.setAttribute('aria-label', `Open LEGEND meme ${i}`);
  card.innerHTML = `<img loading="lazy" src="assets/memes/legend-meme-${n}.jpg" alt="LEGEND meme concept ${i}"><div class="meme-caption">MEME ${n}<span>VIEW ↗</span></div>`;
  card.addEventListener('click', () => openMeme(i));
  grid.appendChild(card);
}

function openMeme(index) {
  activeMeme = ((index - 1 + 20) % 20) + 1;
  const n = String(activeMeme).padStart(2, '0');
  lightboxImage.src = `assets/memes/legend-meme-${n}.jpg`;
  lightboxImage.alt = `LEGEND meme concept ${activeMeme}`;
  lightboxCaption.textContent = `LEGEND MEME ${n} / 20`;
  lightbox.classList.add('open');
  lightbox.setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';
  lightbox.querySelector('.lightbox-close').focus();
}
function closeMeme() {
  lightbox.classList.remove('open');
  lightbox.setAttribute('aria-hidden', 'true');
  document.body.style.overflow = '';
}
lightbox.querySelector('.lightbox-close').addEventListener('click', closeMeme);
lightbox.querySelector('.lightbox-prev').addEventListener('click', () => openMeme(activeMeme - 1));
lightbox.querySelector('.lightbox-next').addEventListener('click', () => openMeme(activeMeme + 1));
lightbox.addEventListener('click', event => { if (event.target === lightbox) closeMeme(); });
document.addEventListener('keydown', event => {
  if (!lightbox.classList.contains('open')) return;
  if (event.key === 'Escape') closeMeme();
  if (event.key === 'ArrowLeft') openMeme(activeMeme - 1);
  if (event.key === 'ArrowRight') openMeme(activeMeme + 1);
});

const menu = document.querySelector('.menu');
const nav = document.querySelector('#navlinks');
menu.addEventListener('click', () => {
  const open = nav.classList.toggle('open');
  menu.setAttribute('aria-expanded', String(open));
});
nav.querySelectorAll('a').forEach(a => a.addEventListener('click', () => {
  nav.classList.remove('open');
  menu.setAttribute('aria-expanded', 'false');
}));

// Copy the publicly documented Robinhood Chain ID.
document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
  const value = button.dataset.copy;
  try {
    await navigator.clipboard.writeText(value);
    button.textContent = 'Copied';
  } catch {
    button.textContent = value;
  }
  window.setTimeout(() => { button.textContent = 'Copy'; }, 1600);
}));

// Reveal sections and meme tiles as they enter the viewport; respect reduced motion.
const revealTargets = document.querySelectorAll('.hero-copy, .hero-art, .mission-grid > *, .story > *, .section-head, .principle-grid article, .manifesto-inner, .community-card, .token > *, .faq-grid > *, .meme-card');
revealTargets.forEach(el => el.classList.add('reveal'));
if ('IntersectionObserver' in window && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12, rootMargin: '0px 0px -30px 0px' });
  revealTargets.forEach(el => observer.observe(el));
} else {
  revealTargets.forEach(el => el.classList.add('is-visible'));
}
