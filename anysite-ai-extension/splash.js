// ═══ Particle canvas on splash ═══
let rafAnimation;
(function () {
  const canvas = document.getElementById("splash-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  let particles = [];

  function resize() {
    canvas.width = canvas.offsetWidth || window.innerWidth;
    canvas.height = canvas.offsetHeight || window.innerHeight;
  }
  resize();
  window.addEventListener("resize", resize);

  class Particle {
    constructor() {
      this.reset();
    }
    reset() {
      this.x = Math.random() * canvas.width;
      this.y = Math.random() * canvas.height;
      this.r = Math.random() * 2 + 0.5;
      this.vx = (Math.random() - 0.5) * 0.6;
      this.vy = (Math.random() - 0.5) * 0.6;
      this.alpha = Math.random() * 0.3 + 0.1;
    }
    update() {
      this.x += this.vx;
      this.y += this.vy;
      if (this.x < 0 || this.x > canvas.width) this.vx *= -1;
      if (this.y < 0 || this.y > canvas.height) this.vy *= -1;
    }
    draw() {
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(255,255,255,${this.alpha})`;
      ctx.fill();
    }
  }

  for (let i = 0; i < 50; i++) particles.push(new Particle());

  function drawLines() {
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 80) {
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.strokeStyle = `rgba(255,255,255,${0.08 * (1 - dist / 80)})`;
          ctx.lineWidth = 0.5;
          ctx.stroke();
        }
      }
    }
  }

  function animate() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    particles.forEach((p) => {
      p.update();
      p.draw();
    });
    drawLines();
    rafAnimation = requestAnimationFrame(animate);
  }
  animate();
})();

// ═══ Splash hide logic ═══
window._splashHidden = false;
window.hideSplash = function () {
  if (window._splashHidden) return;
  window._splashHidden = true;

  const splash = document.getElementById("splash");
  const app = document.getElementById("app");

  if (splash) {
    splash.classList.add("hide");
    setTimeout(() => {
      splash.style.display = "none";
      splash.remove();
      if (typeof rafAnimation !== "undefined")
        cancelAnimationFrame(rafAnimation);
    }, 550);
  }

  if (app) {
    app.style.opacity = "1";
    app.style.pointerEvents = "auto";
    app.classList.add("visible");
  }
};

// ═══ Button ripple effect ═══
document.getElementById("btn").addEventListener("click", function (e) {
  const rect = this.getBoundingClientRect();
  const ripple = document.createElement("span");
  ripple.className = "ripple";
  ripple.style.left = e.clientX - rect.left + "px";
  ripple.style.top = e.clientY - rect.top + "px";
  ripple.style.width = ripple.style.height = "40px";
  this.appendChild(ripple);
  setTimeout(() => ripple.remove(), 500);
});
