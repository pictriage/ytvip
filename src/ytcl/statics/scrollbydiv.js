
function bisectLeft(arr, predicate) {
    /* binary search, faster than linear */
    let lo = 0;
    let hi = arr.length;
    while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (!predicate(arr[mid])) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    return lo;
}

function scrollByDiv(scrollDivs, delta) {
    let scrollY = window.scrollY;

    let newIndex;
    if (delta > 0 && scrollDivs[0].offsetTop > scrollY + 100) {
        // it's jarring if you jump to the first one.
        return false;
    } else {
        if (delta > 0) {
            function predicate(div) {
                let offsetBottom = div.offsetTop + div.offsetHeight;
                return offsetBottom - 5 > scrollY;

            }
        } else {
            function predicate(div) {
                return div.offsetTop + 5 > scrollY;

            }
        }

        let currentIndex = bisectLeft(scrollDivs, predicate);
        // bisect can return the length of the array if all elements are less
        if (currentIndex >= scrollDivs.length) currentIndex = scrollDivs.length - 1;
        let i;
        for (i = currentIndex + delta; i >= 0 && i < scrollDivs.length; i += delta) {
            if (scrollDivs[i].offsetTop !== scrollDivs[currentIndex].offsetTop) break;
        }
        newIndex = i;

        if (newIndex < 0) return false;
    }
    if (newIndex >= scrollDivs.length) return false;

    scrollDivs[newIndex].scrollIntoView({ block: "start" });

    return true;
}

function arrowKeyEventScrollByDiv(event, scrollDivs) {
    let shouldPreventDefault = false;
    if (event.key == "ArrowUp") {
        shouldPreventDefault = scrollByDiv(scrollDivs, -1);
    }
    if (event.key == "ArrowDown") {
        shouldPreventDefault = scrollByDiv(scrollDivs, 1);
    }
    // allow scrolling if it's outside the bounds of the grid
    if (shouldPreventDefault) event.preventDefault();
}

function wheelEventScrollByDiv(event, scrollDivs, numCols) {
    if (event.ctrlKey) return;
    let delta = (event.deltaY > 0) ? 1 : -1;
    let shouldPreventDefault = scrollByDiv(scrollDivs, delta);
    if (shouldPreventDefault) {
        event.preventDefault();
        event.stopPropagation();
    }
}

// scroll-by-div doesn't make sense
// when you have a trackpad.

/*
let scrollDivs;
document.addEventListener('DOMContentLoaded', function () {
    scrollDivs = document.querySelectorAll('.scrollbydiv');

});

document.addEventListener("wheel", function (event) {
    wheelEventScrollByDiv(event, scrollDivs);
}, { passive: false });

window.addEventListener('keydown', function (event) {
    arrowKeyEventScrollByDiv(event, scrollDivs);
});
*/