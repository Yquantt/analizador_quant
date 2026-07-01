-- Strategy governance validation queries.
-- These queries are read-only and are also exposed by database.get_governance_validation_queries().

-- Sistemas sin deployment activo.
SELECT sd.strategy_id, sd.name
  FROM strategy_definitions sd
 WHERE NOT EXISTS (
       SELECT 1 FROM strategy_deployments dep
        WHERE dep.strategy_id = sd.strategy_id
          AND dep.valid_to IS NULL
 );

-- Instancias sin estrategia logica.
SELECT si.instance_id, si.strategy_id
  FROM strategy_instances si
  LEFT JOIN strategy_definitions sd ON sd.strategy_id = si.strategy_id
 WHERE sd.strategy_id IS NULL;

-- Sistemas con mas de una fuente oficial activa.
SELECT strategy_id, COALESCE(strategy_version_id, -1) AS strategy_version_key, COUNT(*) AS active_official_sources
  FROM strategy_sources
 WHERE is_official = 1 AND valid_to IS NULL
 GROUP BY strategy_id, COALESCE(strategy_version_id, -1)
HAVING COUNT(*) > 1;

-- Deployments activos duplicados por instancia.
SELECT instance_id, COUNT(*) AS active_deployments
  FROM strategy_deployments
 WHERE valid_to IS NULL
 GROUP BY instance_id
HAVING COUNT(*) > 1;

-- Instancias activas sin rol.
SELECT dep.id, dep.instance_id, dep.strategy_id
  FROM strategy_deployments dep
 WHERE dep.valid_to IS NULL
   AND (dep.role IS NULL OR dep.role = '');

-- Estrategias en production sin fuente oficial.
SELECT dep.id, dep.strategy_id, dep.instance_id
  FROM strategy_deployments dep
 WHERE dep.valid_to IS NULL
   AND dep.state = 'production'
   AND NOT EXISTS (
       SELECT 1 FROM strategy_sources ss
        WHERE ss.strategy_id = dep.strategy_id
          AND COALESCE(ss.strategy_version_id, -1) = COALESCE(dep.strategy_version_id, -1)
          AND ss.is_official = 1
          AND ss.valid_to IS NULL
   );

-- Estrategias en production sin snapshot reciente.
SELECT dep.id, dep.strategy_id, dep.instance_id
  FROM strategy_deployments dep
 WHERE dep.valid_to IS NULL
   AND dep.state = 'production'
   AND NOT EXISTS (
       SELECT 1 FROM strategy_metric_snapshots sms
        WHERE sms.instance_id = dep.instance_id
          AND sms.calculated_at >= datetime('now', '-24 hours')
   );

-- Decisiones append-only sin evidencia.
SELECT id, entity_type, entity_id, decision_type
  FROM decisions_append_only
 WHERE evidence_json IS NULL
    OR evidence_json = ''
    OR evidence_json = '{}';

-- Fuentes oficiales vencidas.
SELECT id, strategy_id, strategy_version_id, valid_from, valid_to
  FROM strategy_sources
 WHERE valid_to IS NOT NULL
   AND valid_to <= datetime('now')
   AND is_official = 1;
