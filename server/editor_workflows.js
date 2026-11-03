// Shared request and error handling for staff authoring pages.
let staffSession = null;
async function editorFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if ((options.method || 'GET').toUpperCase() !== 'GET') {
    if (!staffSession) {
      staffSession = await (await editorFetch('/support/session')).json();
    }
    headers.set('X-Staff-CSRF', staffSession.csrf);
  }
  const response = await fetch(path, {...options, headers, credentials: 'same-origin'});
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try {
      const error = await response.json();
      message = error.msg || error.message || error.error || message;
    } catch (_) {}
    if (response.status === 401) message = 'Session expired. Sign in again before saving.';
    throw new Error(message);
  }
  return response;
}
window.addEventListener('unhandledrejection', event => {
  event.preventDefault();
  setStatus(event.reason?.message || 'Request failed. Your edits are still on this page.', false);
});
