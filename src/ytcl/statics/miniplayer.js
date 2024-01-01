let observer;
let miniplayers;

function pauseAllMiniplayers() {
  for (let player of miniplayers) {
    player.pause();
  }
}

/*
Ideally we would resume visible miniplayers when the window is focused again,
but this is complicated because we need the IntersectionObserver .isIntersecting
info, but there is no wa to force this to run manually. it happens automatically
on scroll. unless you unobserve and then observe again.
*/

document.addEventListener('DOMContentLoaded', function () {
  miniplayers = document.getElementsByClassName('preview');

  for (let ele of miniplayers) {
    let observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        let isPlaying = !ele.paused;
        let isInView = entry.isIntersecting;
        let windowHasFocus = document.hasFocus();
        if (!isPlaying && isInView && windowHasFocus) {
          // load it lazy here because otherwise seems to
          // cause perf issues when many videos on a page.
          if (!ele.src) {
            ele.src = ele.dataset.src;
          }
          ele.play();
        }
        if (isPlaying && !(isInView && windowHasFocus)) {
          ele.pause();
        }
      });
    }, {});
    observer.observe(ele);
  }

  window.addEventListener('blur', pauseAllMiniplayers)

});

