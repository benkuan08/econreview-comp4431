// The scoped fetch carries requests from the tab back to the notebook's private workspace.
let workspaceRequestId = 0;
async function callWorkspace(operation, ...args) {
  const port = window.econreviewPort;
  if (port) {
    const id = ++workspaceRequestId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        port.removeEventListener('message', receive);
        reject(new Error('Colab did not respond. Keep the notebook connected and rerun Open EconReview.'));
      }, 30000);
      function receive({data}) {
        if (data.id !== id) return;
        clearTimeout(timer);
        port.removeEventListener('message', receive);
        if (data.error) reject(new Error(data.error));
        else resolve(data.value);
      }
      port.addEventListener('message', receive);
      port.start();
      port.postMessage({id, operation, args});
    });
  }
  const kernel = window.google?.colab?.kernel;
  if (!kernel?.invokeFunction) {
    throw new Error('Open EconReview from its connected Colab notebook.');
  }
  const reply = await kernel.invokeFunction('econreview.workspace', [operation, ...args], {});
  const data = reply?.data?.['application/json'];
  if (!data) throw new Error('The workspace did not respond. Check the Colab backend cell.');
  return data;
}

function encodeWorkspaceChunk(bytes) {
  let binary = '';
  for (let start = 0; start < bytes.length; start += 8192) {
    binary += String.fromCharCode(...bytes.subarray(start, start + 8192));
  }
  return btoa(binary);
}

async function requestWorkspace(path, options) {
  const request = new Request(`http://127.0.0.1:8000${path}`, options);
  const body = request.body ? new Uint8Array(await request.arrayBuffer()) : null;
  if (body) {
    for (let start = 0; start < body.length; start += previewChunkSize) {
      const chunk = body.subarray(start, start + previewChunkSize);
      await callWorkspace('append', encodeWorkspaceChunk(chunk), start === 0);
    }
  }
  const contentType = request.headers.get('Content-Type');
  const reply = await callWorkspace('request', request.method, path, contentType, Boolean(body?.length));
  const bytes = new Uint8Array(reply.length);
  bytes.set(Uint8Array.from(atob(reply.data), char => char.charCodeAt(0)));
  for (let start = previewChunkSize; start < bytes.length; start += previewChunkSize) {
    const chunk = await callWorkspace('read', start);
    bytes.set(Uint8Array.from(atob(chunk.data), char => char.charCodeAt(0)), start);
  }
  const headers = reply.type ? {'Content-Type': reply.type} : {};
  return new Response(bytes.length ? bytes : null, {status: reply.status, headers});
}

// Keep each request's upload and response chunks together, even if the app
// calls fetch concurrently while loading a page.
let pendingWorkspaceRequest = Promise.resolve();
function fetch(path, options) {
  const pending = pendingWorkspaceRequest.then(() => requestWorkspace(path, options));
  pendingWorkspaceRequest = pending.catch(() => {});
  return pending;
}
