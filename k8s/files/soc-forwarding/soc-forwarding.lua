-- Turns the soc.* copies of audit and detection records into the fields the SOC
-- syslog output sends: the event as JSON (soc_message), its source (soc_appname),
-- node (soc_hostname), type (soc_msgid) and severity. Field mapping:
-- docs/guides/soc-forwarding.md.

-- Collection metadata, not part of the event.
local TRANSPORT = { kubernetes = true, kubernetes_namespace = true, stream = true, _p = true, time = true, log = true }

local function encode_string(text)
  return '"' .. text:gsub('[%c"\\]', function(character)
    local escapes = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
    return escapes[character] or string.format('\\u%04x', character:byte())
  end) .. '"'
end

local encode

local function encode_table(value)
  local keys, count = {}, 0
  for key in pairs(value) do
    count = count + 1
    keys[count] = key
  end
  -- A table holding exactly 1..n is a JSON array; an empty one is an object.
  if count > 0 and count == #value then
    local items = {}
    for position = 1, count do items[position] = encode(value[position]) end
    return '[' .. table.concat(items, ',') .. ']'
  end
  table.sort(keys, function(left, right) return tostring(left) < tostring(right) end)
  local members = {}
  for position, key in ipairs(keys) do
    members[position] = encode_string(tostring(key)) .. ':' .. encode(value[key])
  end
  return '{' .. table.concat(members, ',') .. '}'
end

encode = function(value)
  local kind = type(value)
  if kind == 'table' then return encode_table(value) end
  if kind == 'string' then return encode_string(value) end
  if kind == 'boolean' then return tostring(value) end
  if kind == 'number' then
    if value ~= value or value == math.huge or value == -math.huge then return 'null' end
    if value == math.floor(value) and math.abs(value) < 2 ^ 53 then return string.format('%d', value) end
    return string.format('%.17g', value)
  end
  return 'null'
end

-- RFC 5424 MSGID: at most 32 printable characters without spaces.
local function message_id(value)
  if type(value) ~= 'string' or value == '' then return '-' end
  return (value:gsub('[^%w._:-]', '_'):sub(1, 32))
end

function soc_record(tag, timestamp, record)
  local source = tag:match('^soc%.(.+)$')
  local kubernetes = type(record['kubernetes']) == 'table' and record['kubernetes'] or {}
  local host, id, severity, event = kubernetes['host'], nil, 6, record
  if source == 'identity' then
    local keycloak = type(record['keycloak']) == 'table' and record['keycloak'] or {}
    id = keycloak['type'] or keycloak['operationType']
    if keycloak['error'] ~= nil then severity = 5 end
  elseif source == 'kubernetes-audit' then
    host, id = record['node'], record['verb']
    local status = type(record['responseStatus']) == 'table' and tonumber(record['responseStatus']['code'])
    if status and status >= 400 then severity = 5 end
  elseif source == 'vault-audit' then
    -- Vault's own log lines share the container; only its audit entries count.
    if kubernetes['namespace_name'] ~= 'infra' or type(record['request']) ~= 'table'
        or (record['type'] ~= 'request' and record['type'] ~= 'response') then
      return -1, timestamp, record
    end
    id = record['type']
    if record['error'] ~= nil then severity = 5 end
  elseif source == 'detection' then
    event = record['security_finding']
    if kubernetes['namespace_name'] ~= 'infra' or type(event) ~= 'table' then
      return -1, timestamp, record
    end
    host, id = event['node'] or host, event['alertname']
    severity = event['severity'] == 'critical' and 2 or 4
  elseif source == 'host-journal' then
    host, id = record['hostname'], record['syslog_identifier']
  else
    return -1, timestamp, record
  end
  local fields = {}
  for key, value in pairs(event) do
    if not TRANSPORT[key] then fields[key] = value end
  end
  return 2, timestamp, {
    soc_message = encode({ source = source, node = host, event = fields }),
    soc_appname = 'swirl-cloud-' .. source,
    soc_hostname = type(host) == 'string' and host ~= '' and host or '-',
    soc_msgid = message_id(id),
    -- A string: Lua numbers reach the output as floats, which it cannot read as a severity.
    soc_severity = tostring(severity),
  }
end
