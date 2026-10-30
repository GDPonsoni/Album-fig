// Degradação suave: em aparelho fraco a figurinha aparece sem animação.
let low = false;

function apply(value: boolean) {
  low = value;
  document.documentElement.dataset.fx = value ? 'low' : 'full';
}

export const lowFx = () => low;

export function initFx() {
  const memory = (navigator as Navigator & { deviceMemory?: number }).deviceMemory;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  apply(reduced || (memory !== undefined && memory <= 1));
  if (low) return;

  // Mede o ritmo real de quadros logo depois de carregar.
  let frames = 0;
  let start = 0;
  const tick = (time: number) => {
    if (!start) start = time;
    frames += 1;
    if (frames < 45) requestAnimationFrame(tick);
    else if ((time - start) / frames > 40) apply(true);
  };
  window.setTimeout(() => requestAnimationFrame(tick), 1500);
}

export function buzz(pattern: number | number[]) {
  navigator.vibrate?.(pattern);
}
