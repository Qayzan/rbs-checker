// bookmarklet.js
// To use: copy the javascript: line below and save it as a browser bookmark.
// Click it while logged into RBS to copy your session cookies to clipboard.

// Paste this as the URL of a new bookmark:
javascript:(function(){
  var cookies = document.cookie;
  if (!cookies) {
    alert('No cookies found. Make sure you are logged into RBS first.');
    return;
  }
  navigator.clipboard.writeText(cookies).then(function() {
    alert('\u2705 Cookie copied! Paste it into the Telegram bot.');
  }).catch(function() {
    prompt('Copy this cookie string:', cookies);
  });
})();
