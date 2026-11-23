/**
 * Единая нормализация клиентского состояния подключения Google Antigravity
 * для AiConnections, AgentSettings, ModelPicker, Integrations и ArrodesWorkspace.
 */

export function formatSafeDateTime(raw, { timeOnly = false } = {}) {
  if (!raw || typeof raw !== 'string') return null;
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return null;
  return timeOnly ? parsed.toLocaleTimeString() : parsed.toLocaleString();
}

export function normalizeAntigravityState({ data = null, stateQuery = null } = {}) {
  const status = stateQuery?.data ?? null;
  const hasAnySnapshot = Boolean(status || data);
  const isFetching = Boolean(stateQuery?.isFetching);
  const isPending = Boolean(stateQuery?.isPending) && !hasAnySnapshot;
  const queryError = stateQuery?.error?.message || null;

  const systemEnabled = hasAnySnapshot ? Boolean((status?.system_enabled ?? data?.system_enabled) === true) : false;
  const systemConfigured = hasAnySnapshot ? Boolean((status?.system_configured ?? data?.system_configured) === true) : false;
  const oauthClientMismatch = Boolean(status?.oauth_client_mismatch ?? data?.oauth_client_mismatch);
  const hasSession = Boolean(
    status?.oauth_authorized
    ?? status?.authorized
    ?? (!oauthClientMismatch && (data?.configured?.access_token || data?.configured?.refresh_token)),
  );
  const hasRefreshToken = Boolean(status?.has_refresh_token ?? data?.configured?.refresh_token);
  const tokenExpired = Boolean(status?.token_expired);
  const catalogAccessible = Boolean(status ? status.catalog_accessible : data?.catalog_accessible);
  const configStale = Boolean(status?.config_stale ?? data?.config_stale);
  const generationVerified = Boolean(status ? status.generation_verified : data?.generation_verified);
  const verifiedModel = status?.generation_verified_model || data?.generation_verified_model || '';
  const verifiedAt = status?.generation_verified_at || data?.generation_verified_at || null;
  const includeInternal = Boolean(status?.include_internal ?? data?.include_internal);
  const connectionId = String(status?.connection_id ?? (!oauthClientMismatch ? (data?.connection_id || '') : ''));
  const configVersion = status?.config_version ?? data?.config_version ?? null;
  const accountEmail = status?.account_email || data?.account_email || '';
  const accountName = status?.account_name || data?.account_name || '';
  const projectId = status?.project_id || data?.discovered_project_id || '';
  const tier = status?.subscription_tier || data?.subscription_tier || '';
  const models = Array.isArray(status?.models)
    ? status.models
    : (Array.isArray(data?.cached_models) && !oauthClientMismatch ? data.cached_models : []);

  const oauthError = status?.oauth_error || null;
  const transportError = status?.transport_error || queryError || null;
  const quotaError = status?.quota_error || null;
  const serverError = (!oauthError && !transportError && !quotaError)
    ? (status?.error || data?.last_error || null)
    : null;
  const refreshWarning = status?.refresh_warning
    || (hasSession && !hasRefreshToken
      ? 'Google не выдал refresh_token — автоматическое обновление токена недоступно; после истечения потребуется повторный вход.'
      : null);

  let phase = 'loading';
  let badgeVariant = 'outline';
  let badgeText = 'Проверка…';

  if (isPending) {
    phase = 'loading';
    badgeVariant = 'outline';
    badgeText = 'Проверка…';
  } else if (!systemEnabled) {
    phase = 'disabled';
    badgeVariant = 'warning';
    badgeText = 'Отключён админом';
  } else if (!systemConfigured) {
    phase = 'unconfigured';
    badgeVariant = 'warning';
    badgeText = 'Не настроен админом';
  } else if (oauthClientMismatch) {
    phase = 'oauth_mismatch';
    badgeVariant = 'warning';
    badgeText = 'Требуется переподключение';
  } else if (!hasSession) {
    phase = 'disconnected';
    badgeVariant = 'outline';
    badgeText = 'Не подключён';
  } else if (isFetching && !status) {
    phase = 'loading';
    badgeVariant = 'outline';
    badgeText = 'Проверка…';
  } else if (oauthError) {
    phase = 'oauth_error';
    badgeVariant = 'warning';
    badgeText = 'Ошибка OAuth';
  } else if (tokenExpired && !hasRefreshToken) {
    phase = 'token_expired';
    badgeVariant = 'warning';
    badgeText = 'Токен истёк';
  } else if (transportError) {
    phase = 'transport_error';
    badgeVariant = 'warning';
    badgeText = 'Ошибка соединения';
  } else if (quotaError) {
    phase = 'quota_error';
    badgeVariant = 'warning';
    badgeText = 'Ошибка квоты';
  } else if (configStale) {
    phase = 'config_stale';
    badgeVariant = 'warning';
    badgeText = 'Требуется перепроверка';
  } else if (!catalogAccessible) {
    phase = 'catalog_unavailable';
    badgeVariant = 'warning';
    badgeText = 'Нет доступа к каталогу';
  } else if (generationVerified) {
    phase = 'verified';
    badgeVariant = 'success';
    badgeText = 'Подтверждён';
  } else {
    phase = 'catalog_ok';
    badgeVariant = 'outline';
    badgeText = 'Каталог доступен';
  }

  const canUseCatalog = Boolean(
    hasAnySnapshot
    && !isPending
    && systemEnabled
    && systemConfigured
    && hasSession
    && !oauthClientMismatch
    && !oauthError
    && !(tokenExpired && !hasRefreshToken),
  );

  const canRunAgent = Boolean(
    canUseCatalog
    && catalogAccessible
    && !quotaError,
  );

  const primaryError = oauthError || transportError || quotaError || serverError || null;

  return {
    phase,
    isLoading: isPending || Boolean(isFetching && !status && hasSession),
    hasSnapshot: hasAnySnapshot,
    systemEnabled,
    systemConfigured,
    oauthAuthorized: hasSession && !oauthClientMismatch,
    oauthClientMismatch,
    hasRefreshToken,
    tokenExpired,
    catalogAccessible,
    configStale,
    generationVerified,
    verifiedModel,
    verifiedAt,
    includeInternal,
    connectionId,
    configVersion,
    accountEmail,
    accountName,
    projectId,
    tier,
    models,
    checkedAt: status?.checked_at || data?.catalog_checked_at || null,
    tokenExpiresAt: status?.token_expires_at || null,
    lastSuccess: status?.last_success || data?.last_success || null,
    oauthError,
    transportError,
    quotaError,
    serverError,
    primaryError,
    refreshWarning,
    badgeVariant,
    badgeText,
    canUseCatalog,
    canRunAgent,
  };
}

export function getModelAvailability(models, modelId, { catalogLoaded = false, generationVerified = false, verifiedModel = '' } = {}) {
  const target = (modelId || '').trim();
  if (!target) {
    return {
      selected: false,
      knownInCatalog: false,
      available: false,
      quotaKnown: false,
      remainingPercent: null,
      resetText: null,
      generationVerifiedForModel: false,
      warning: null,
    };
  }
  const list = Array.isArray(models) ? models : [];
  const found = list.find((m) => m && m.id === target) || null;
  const quotaKnown = Boolean(found && found.remaining_percent != null);
  const remainingPercent = quotaKnown ? found.remaining_percent : null;
  const resetText = found?.reset_time ? formatSafeDateTime(found.reset_time) : null;
  const knownInCatalog = Boolean(found);
  const available = Boolean(found && found.available !== false && (remainingPercent == null || remainingPercent > 0));
  const generationVerifiedForModel = Boolean(generationVerified && (!verifiedModel || verifiedModel === target));

  let warning = null;
  if (found) {
    if (found.available === false || remainingPercent === 0) {
      warning = resetText
        ? `Модель «${target}» сейчас недоступна или квота исчерпана (сброс: ${resetText}).`
        : `Модель «${target}» сейчас недоступна или квота исчерпана.`;
    }
  } else if (catalogLoaded) {
    warning = `Модель «${target}» отсутствует в загруженном каталоге Cloud Code: доступность и квота не подтверждены.`;
  }

  return {
    selected: true,
    knownInCatalog,
    available,
    quotaKnown,
    remainingPercent,
    resetText,
    generationVerifiedForModel,
    warning,
    meta: found,
  };
}
