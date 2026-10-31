if (
  location.pathname === '/' &&
  ['community', 'view', 'guide', 'queue'].some((k) => new URL(location.href).searchParams.has(k))
)
  location.replace('/app/' + location.search + location.hash);
