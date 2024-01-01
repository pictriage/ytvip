function clickedDownload(btn) {
  let $btn = $(btn);
  console.log('clicked to download video')
  $.post({
      url: '/download',
      data: JSON.stringify({ytid: btn.dataset.ytid, channel_id: btn.dataset.channel_id}),
      contentType : 'application/json',
      success: function () {
          $btn.text('queued');
      },
  });
}

function vlc(btn) {
  let $btn = $(btn);
  $.post({
      url: '/mpv',
      data: {path: btn.value},
  });
}
