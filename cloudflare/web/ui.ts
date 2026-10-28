export const esc = (value: unknown) =>
  String(value).replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!,
  );

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId?: string,
    public issues: { path: (string | number)[]; message: string }[] = [],
  ) {
    super(`${message}${requestId ? ` (Request ${requestId})` : ''}`);
  }
}

export async function requestJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set('Content-Type', 'application/json');
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
  const parsed = await response.json().catch(() => null);
  const body = parsed && typeof parsed === 'object' ? parsed : {};
  if (!response.ok)
    throw new ApiError(
      response.status,
      body.code ?? 'request_failed',
      body.error ?? 'Request failed.',
      body.requestId ?? response.headers.get('X-Request-ID') ?? undefined,
      body.issues,
    );
  return body as T;
}

/** Resolve focus again after a render replaces the original control. */
export function rememberFocus(target = document.activeElement as HTMLElement | null) {
  const form = target?.closest<HTMLFormElement>('form[data-config]')?.dataset.config;
  const name = target?.getAttribute('name');
  const attribute = [
    'data-edit',
    'data-action',
    'data-view',
    'data-help-title',
    'data-queue-select',
  ].find((key) => target?.hasAttribute(key));
  const selector = target?.id
    ? `#${CSS.escape(target.id)}`
    : form && name
      ? `[data-config="${CSS.escape(form)}"] [name="${CSS.escape(name)}"]`
      : attribute
        ? `[${attribute}="${CSS.escape(target!.getAttribute(attribute)!)}"]`
        : '';
  return () => {
    const candidates = [
      target,
      ...(selector ? document.querySelectorAll<HTMLElement>(selector) : []),
      ...document.querySelectorAll<HTMLElement>(
        '[data-edit="drawer"], #community, [data-view][aria-selected="true"]',
      ),
    ];
    candidates
      .find(
        (el) =>
          el?.isConnected &&
          el !== document.body &&
          !el.matches(':disabled') &&
          el.getClientRects().length,
      )
      ?.focus();
  };
}

/** A native top-layer dialog keeps focus and fixed overlays outside page animations. */
export function openDialog(title: string, content: string, kind: 'drawer' | 'sheet' = 'sheet') {
  document.querySelector<HTMLDialogElement>('#app-dialog')?.close();
  const restoreFocus = rememberFocus();
  const dialog = document.createElement('dialog');
  dialog.id = 'app-dialog';
  dialog.className = kind;
  dialog.setAttribute('aria-labelledby', 'dialog-title');
  dialog.innerHTML = `<div class="dialog-heading"><h2 id="dialog-title">${esc(title)}</h2><button type="button" data-close aria-label="Close ${esc(title)}">Close</button></div><div class="dialog-content">${content}</div>`;
  dialog.querySelector('[data-close]')!.addEventListener('click', () => dialog.close());
  dialog.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab') return;
    const items = [
      ...dialog.querySelectorAll<HTMLElement>(
        'button, a[href], input, select, textarea, [tabindex]',
      ),
    ].filter(
      (item) => item.tabIndex >= 0 && !item.matches(':disabled') && item.getClientRects().length,
    );
    const first = items[0],
      last = items.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  });
  dialog.addEventListener('click', (e) => {
    if (e.target === dialog) {
      const r = dialog.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)
        dialog.close();
    }
  });
  dialog.addEventListener(
    'close',
    () => {
      dialog.remove();
      requestAnimationFrame(restoreFocus);
    },
    { once: true },
  );
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

export function labelTables(root: ParentNode) {
  root.querySelectorAll<HTMLTableElement>('table').forEach((table) => {
    const labels = [...table.querySelectorAll('thead th')].map((th) => th.textContent ?? '');
    table.querySelectorAll('tbody tr').forEach((row) =>
      [...row.children].forEach((cell, i) => {
        if (!labels[i] || cell.querySelector('.cell-label') || cell.getAttribute('colspan')) return;
        const label = document.createElement('span');
        label.className = 'cell-label';
        label.textContent = labels[i];
        label.setAttribute('aria-hidden', 'true');
        cell.prepend(label);
      }),
    );
  });
}

// The visual viewport also shrinks for the on-screen keyboard on iOS.
export function installViewport() {
  const update = () => {
    const viewport = window.visualViewport;
    const keyboard = Boolean(
      viewport && viewport.scale === 1 && innerHeight - viewport.height > 150,
    );
    document.documentElement.classList.toggle('keyboard-open', keyboard);
    document.documentElement.style.setProperty(
      '--viewport-height',
      `${keyboard ? viewport!.height : innerHeight}px`,
    );
  };
  window.visualViewport?.addEventListener('resize', update);
  window.addEventListener('resize', update);
  matchMedia('(max-width: 640px)').addEventListener('change', () =>
    document.querySelector<HTMLDialogElement>('#app-dialog')?.close(),
  );
  update();
}
