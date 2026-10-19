
document.addEventListener("DOMContentLoaded", function () {
  const homeBannerCarousel = document.getElementById("homeBannerCarousel");
  if (homeBannerCarousel) {
    homeBannerCarousel.addEventListener("slide.bs.carousel", function (event) {
      const image = event.relatedTarget.querySelector("img[data-src]");
      if (image) {
        image.src = image.dataset.src;
        image.removeAttribute("data-src");
      }
    });
  }

  document.querySelectorAll(".dropdown-menu").forEach(function (menu) {
    menu.addEventListener("click", function (event) {
      event.stopPropagation();
    });
  });

  if (window.bootstrap && window.bootstrap.Tooltip) {
    document.querySelectorAll('[data-bs-toggle="tooltip"], [data-toggle="tooltip"]')
      .forEach(function (element) {
        new window.bootstrap.Tooltip(element);
      });
  }
});
