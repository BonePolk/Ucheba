import assert from 'node:assert/strict';
import test from 'node:test';

import {
  formatSafeDateTime,
  getModelAvailability,
  normalizeAntigravityState,
} from '../frontend/src/lib/antigravity.js';

test('formatSafeDateTime rejects invalid dates and formats valid ISO timestamps', () => {
  assert.equal(formatSafeDateTime(null), null);
  assert.equal(formatSafeDateTime('not-a-date'), null);
  const formatted = formatSafeDateTime('2026-11-24T12:00:00Z');
  assert.equal(typeof formatted, 'string');
  assert.ok(formatted.length > 4);
});

test('normalizeAntigravityState distinguishes loading, admin unconfigured, disconnected, and mismatch', () => {
  const loading = normalizeAntigravityState({ data: null, stateQuery: { isPending: true } });
  assert.equal(loading.phase, 'loading');
  assert.equal(loading.canRunAgent, false);
  assert.equal(loading.canUseCatalog, false);

  const disabled = normalizeAntigravityState({
    data: { system_enabled: false, system_configured: true },
  });
  assert.equal(disabled.phase, 'disabled');
  assert.equal(disabled.canRunAgent, false);

  const unconf = normalizeAntigravityState({
    data: { system_enabled: true, system_configured: false },
  });
  assert.equal(unconf.phase, 'unconfigured');
  assert.equal(unconf.canUseCatalog, false);

  const mismatch = normalizeAntigravityState({
    stateQuery: {
      data: {
        system_enabled: true,
        system_configured: true,
        oauth_client_mismatch: true,
        oauth_authorized: false,
        catalog_accessible: false,
      },
    },
  });
  assert.equal(mismatch.phase, 'oauth_mismatch');
  assert.equal(mismatch.canRunAgent, false);
  assert.equal(mismatch.canUseCatalog, false);
});

test('normalizeAntigravityState handles transport error, quota error, config stale, catalog_ok vs verified', () => {
  const netErr = normalizeAntigravityState({
    stateQuery: {
      data: {
        system_enabled: true,
        system_configured: true,
        oauth_authorized: true,
        has_refresh_token: true,
        catalog_accessible: false,
        transport_error: 'Сеть недоступна',
      },
    },
  });
  assert.equal(netErr.phase, 'transport_error');
  assert.equal(netErr.canRunAgent, false);
  assert.equal(netErr.primaryError, 'Сеть недоступна');

  const stale = normalizeAntigravityState({
    stateQuery: {
      data: {
        system_enabled: true,
        system_configured: true,
        oauth_authorized: true,
        has_refresh_token: false,
        catalog_accessible: true,
        config_stale: true,
      },
    },
  });
  assert.equal(stale.phase, 'config_stale');
  assert.ok(stale.refreshWarning);

  const catOk = normalizeAntigravityState({
    stateQuery: {
      data: {
        system_enabled: true,
        system_configured: true,
        oauth_authorized: true,
        has_refresh_token: true,
        catalog_accessible: true,
        generation_verified: false,
        connection_id: 'conn-1',
        config_version: 2,
      },
    },
  });
  assert.equal(catOk.phase, 'catalog_ok');
  assert.equal(catOk.canRunAgent, true);
  assert.equal(catOk.generationVerified, false);

  const verified = normalizeAntigravityState({
    stateQuery: {
      data: {
        system_enabled: true,
        system_configured: true,
        oauth_authorized: true,
        has_refresh_token: true,
        catalog_accessible: true,
        generation_verified: true,
        generation_verified_model: 'gemini-4-neon-xhigh',
        connection_id: 'conn-2',
        config_version: 2,
      },
    },
  });
  assert.equal(verified.phase, 'verified');
  assert.equal(verified.badgeVariant, 'success');
  assert.equal(verified.canRunAgent, true);
});

test('getModelAvailability never invents 100% quota and warns on unavailable or manual uncataloged ID', () => {
  const models = [
    { id: 'm-ok', available: true, remaining_percent: null, reset_time: null },
    { id: 'm-zero', available: false, remaining_percent: 0, reset_time: 'invalid-ts' },
  ];
  const okMissingQuota = getModelAvailability(models, 'm-ok', { catalogLoaded: true });
  assert.equal(okMissingQuota.available, true);
  assert.equal(okMissingQuota.quotaKnown, false);
  assert.equal(okMissingQuota.remainingPercent, null);
  assert.equal(okMissingQuota.warning, null);

  const zeroQuota = getModelAvailability(models, 'm-zero', { catalogLoaded: true });
  assert.equal(zeroQuota.available, false);
  assert.equal(zeroQuota.resetText, null);
  assert.match(zeroQuota.warning, /недоступна|исчерпана/);

  const manual = getModelAvailability(models, 'manual-custom-model', { catalogLoaded: true });
  assert.equal(manual.knownInCatalog, false);
  assert.match(manual.warning, /отсутствует/);
});
