/* shrippen Design Default v1 — language switch, nav brand reveal, copy button.
 * Load synchronously in <head> so the stored language applies before first paint. */
(function () {
  var d = document, h = d.documentElement, KEY = 'shrippen-lang';
  h.classList.add('js');

  var lang = 'en'; // English is the default, whatever the browser language is
  try { var s = localStorage.getItem(KEY); if (s === 'de' || s === 'en') lang = s; } catch (e) {}
  h.lang = lang;

  function mark() {
    [].forEach.call(d.querySelectorAll('.lang button'), function (b) {
      b.setAttribute('aria-pressed', String(b.getAttribute('data-lang') === h.lang));
    });
  }

  d.addEventListener('DOMContentLoaded', function () {
    mark();

    d.addEventListener('click', function (e) {
      var lb = e.target.closest && e.target.closest('.lang button');
      if (lb) {
        h.lang = lb.getAttribute('data-lang');
        try { localStorage.setItem(KEY, h.lang); } catch (x) {}
        mark();
        return;
      }
      var cb = e.target.closest && e.target.closest('.copy-btn');
      if (cb) {
        var box = cb.closest('.cmd-row').querySelector('.cmd-box');
        navigator.clipboard.writeText(box.textContent.trim()).then(function () {
          cb.classList.add('copied');
          setTimeout(function () { cb.classList.remove('copied'); }, 1500);
        });
      }
    });

    // Project name in the nav appears once the hero is out of view.
    var nav = d.querySelector('.nav'), hero = d.querySelector('.hero');
    if (nav && hero && 'IntersectionObserver' in window) {
      new IntersectionObserver(function (en) {
        nav.classList.toggle('brand-on', !en[0].isIntersecting);
      }, { rootMargin: '-56px 0px 0px 0px' }).observe(hero);
    } else if (nav) {
      nav.classList.add('brand-on');
    }
  });
})();
