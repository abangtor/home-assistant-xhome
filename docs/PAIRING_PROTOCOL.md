# XHome door pairing protocol

Research date: 2026-09-07. App: **XHome 1.6.4.8**
(`com.lancens.wxdoorbell`). Repository baseline: `f88c922`.

## Finding and scope

For the Wi-Fi door/camera setup paths examined, pairing has three distinct parts:

1. Provision the Wi-Fi connection locally.
2. Wait for the vendor backend to observe the device online.
3. Bind the device to the signed-in account, or complete a cloud-issued QR
   pairing transaction.

**Wi-Fi association is not the same as account ownership or cloud-free operation.**
The AP/BLE Wi-Fi payloads do not contain the account login token. Account binding
is a separate HTTP operation. The QR path additionally embeds a cloud-issued
pairing key.

These findings are from Java/smali and ARM64 native-library analysis, not a new
physical pairing capture. The installed door's exact product/configuration mode
has not been rechecked. Do not assume every supported app mode exists on that
hardware. Standalone BLE-lock enrollment is a separate app path
(`v1/api/user/add/blelock/device`), not evidence that the Wi-Fi camera door can
operate entirely over BLE.

See [local-only feasibility](LOCAL_ONLY_FEASIBILITY.md) for the implications.

## Mode selection

`ConfiguringDeviceActivity.handlerNext()` reads the selected product's
configuration mode and dispatches as follows [J1]:

| Mode | App path | Provisioning transport |
|---:|---|---|
| 1 | `WiFiConfigFirstActivity` → `ConfigWiFiActivity` | Device AP, LAN UDP, HTTP compatibility path |
| 2 | `BleConfigFirstActivity` → BLE discovery/configuration | BLE |
| 4 | `QrConfigFirstActivity` → second/third QR activity | Phone displays Wi-Fi/pairing JSON as a QR image |
| 5 | BLE configuration activities | BLE; selects the newer Wi-Fi payload |

The physical action that enables setup mode is product-specific. The activity
dispatch does not establish a universal keypad sequence or reset procedure.

## AP/BLE pairing sequence

```mermaid
sequenceDiagram
    participant App as XHome app
    participant Door as Door
    participant Cloud as Vendor backend
    Note over App: User signed in; product and region selected
    App->>Door: Discover device in setup mode (AP/LAN or BLE)
    App->>Door: Wi-Fi SSID/password; region in newer payloads
    Door-->>App: Device UID / provisioning response
    Note over Door: Joins configured Wi-Fi
    Door-->>Cloud: Device becomes online (device-side protocol not recovered)
    loop Until backend reports a fresh online observation
        App->>Cloud: GET v1/api/device/online
        Cloud-->>App: online_time, server_time
    end
    App->>Cloud: POST v1/api/user/device (account token + UID)
    Cloud-->>App: Binding result / device information
    opt User assigns name
        App->>Cloud: POST v1/api/user/update/device
    end
```

The diagram's door-to-cloud step is inferred from the app waiting for the
backend's online record. Its actual firmware messages are not in the app source.

## 1. AP / LAN provisioning

### Discovery and UDP envelope

The Java bridge exposes `LANUDP.startSearch("DOORBELL2", "searching")` and
`sendDistributionCmd("DOORBELL2", "config", "wifi_config", configJson)` [J2,J3].

Native analysis establishes [N1–N5]:

- IPv4 UDP destination port **5000** (`0x1388` in network byte order).
- Broadcast destination `255.255.255.255`.
- A unicast discovery pass through last-octet addresses 1–254 using the local
  address's first three octets; this is a hard-coded /24-style sweep, not netmask
  discovery.
- The receive socket binds to the local IPv4 address with port **0**, so the
  client source/response port is ephemeral, not 5000.
- Requests are JSON, with `index` initialized to the string `"1"`.
- Incoming packets with `status == "config"` are echoed to their sender before
  the Java callback is invoked.

Logical discovery request:

```json
{"command":"client_search","code":"DOORBELL2","index":"1","status":"searching","other":""}
```

The `DOORBELL2` code is a public protocol discriminator, not an account
credential. `LANUDP.getUidByJson()` reads `command`, `code`, `index`,
`status`, `uid`, and optionally `other`; its UID syntax check is 20
alphanumeric characters. A discovered UID is an identifier, not proof of
ownership.

### Wi-Fi configuration

`ConfigWiFiActivity.getUid()` creates the following nested object [J3]:

```json
{
  "command": "wifi_config",
  "code": "DOORBELL2",
  "index": "1",
  "status": "config",
  "other": {
    "ssid": "<redacted>",
    "password": "<redacted>",
    "type": "<selected device type>",
    "area": "<decimal provisioning-region code>"
  }
}
```

For `wifi_config`, native code inserts `other` as a **JSON object**, not a
JSON-encoded string. The app sends `type` and `area` as strings.
No hostname, port, server URL, account token, or account ID is added by this
builder.

The examined path is plaintext JSON over local UDP. The library also exposes
`sendDistributionEncCmd`/`sendDistributionEncCmd2` and handles
`wifi_enc_config`; their existence must not be confused with the unencrypted
`sendDistributionCmd` actually called by this activity. No call to those
encrypted entry points was found in the examined app Java setup paths.

A `wifi_config` response, disappearance of the door AP, or a successful
compatibility HTTP response advances the app toward its cloud-online check.
The AP callback distinguishes `client_search` from `wifi_config`; it does
not treat discovery alone as finished pairing.

### HTTP compatibility path

The same activity also tries:

- Destination: `http://192.168.1.1/No_content`
- Method: POST
- Body construction: `__SL_P_ULD=` + SSID + `_SSIDPASSWORD_` + password
- The activity accepts HTTP 201 or 204 as success.

This is a delimiter-based body, not normal key/value form encoding. It contains
neither a custom server address nor the region field. It is an app compatibility
path, not confirmation that this door firmware implements it [J3].

### Implementation constraints

The activity limits AP Wi-Fi passwords to 20 UTF-8 bytes. Native
`sendDistributionCmd` copies its configuration JSON into a 150-byte field;
broadcast formatting supplies a 200-byte checked size, while unicast formatting
uses 512 bytes. These are old-client constraints, not limits to general Wi-Fi.
An independent implementation must enforce sensible packet bounds rather than
copying unchecked formatting or silently truncating input.

Do not record real provisioning bodies in public logs or fixtures: they contain
Wi-Fi credentials. No provisioning packet was transmitted in this investigation.

## 2. BLE Wi-Fi provisioning

### Transport

For the command path used by `BleManager.B0()` [J4,S2]:

| Role | UUID |
|---|---|
| Service | `0000fff0-0000-1000-8000-00805f9b34fb` |
| Command write | `0000fff1-0000-1000-8000-00805f9b34fb` |
| Notifications | `0000fff4-0000-1000-8000-00805f9b34fb` |
| Notification descriptor | `00002902-0000-1000-8000-00805f9b34fb` |

The app also handles other TI-style services for other operations/hardware.
These UUIDs describe the traced Wi-Fi command path, not every BLE product.

### Packet framing

`bleconfig/a.java` (`BleCommand`) constructs [J5]:

| Offset | Meaning |
|---|---|
| 0–1 | Magic `AB CD` |
| 2 | Constant `01` |
| 3–4 | Little-endian length = total packet length minus 7 |
| 5 | Command |
| 6 onward | Payload |
| Final 4–3 bytes | Little-endian additive checksum |
| Final 2 bytes | `0A 0D` |

Checksum = sum of the unsigned bytes from offset 2 through the end of the
payload, stored in two bytes. The receiver can reassemble fragmented
notifications using the length field and footer; a BLE notification is not
necessarily a complete application frame [J4].

### Legacy payload: command 0xA2

The complete packet is 59 bytes, with a 49-byte payload [J5,J6]:

| Payload offset | Size | Meaning |
|---|---:|---|
| 0 | 32 | SSID bytes, zero-padded; builder caps at 32 |
| 32 | 1 | Security value: 0 for empty password, 4 otherwise |
| 33 | 16 | Password bytes, zero-padded; builder caps at 16 |

There is **no region or server field** in this legacy payload.

### Newer payload: command 0x02

The payload is a sequence of length/type/value fields. Length includes the type
byte but excludes the length byte [J6]:

| Type | Value |
|---:|---|
| 1 | SSID bytes |
| 2 | Password bytes; empty value allowed |
| 3 | One-byte security value: 0 if password empty, 4 otherwise |
| 5 | One-byte provisioning-region code |

`BleManager.w0()` selects command 0x02 when mode is 5 or its detected protocol
variant is nonzero; otherwise it selects 0xA2. The activity uses a password limit
of 32 bytes for the newer variant and 16 for the legacy one [J4,J7].

There is no custom server address in either builder. The application-layer
payload contains the Wi-Fi bytes directly; this observation says nothing about
whether a particular Bluetooth link negotiates link-layer encryption.

After a BLE configuration response supplies the UID, the activity performs the
same cloud-online check and account-binding call as AP setup. This is not
Android Bluetooth bonding being used as the ownership database [J7].

## 3. Cloud-online check and account binding

The app polls [J3,J7,J8]:

`GET v1/api/device/online`

Query fields: `uid`, `online_type` (the setup code uses the string `"21"`,
via `PushInfo.TYPE_ONLINE`).
`r.V()` builds this request with an empty account-token argument; this does not
mean the backend's device registration protocol is unauthenticated.

The response model contains `online_id`, `uuid`, `online_time`,
`online_type`, and `server_time`. AP/BLE setup compares:

`abs(phone_now - configuration_start) > abs(server_time - online_time)`

Only then does it advance to binding; otherwise it polls again after about
two seconds. This is a freshness check, not simply “UID exists.”

Binding uses the signed-in account token:

`POST v1/api/user/device`

| JSON field | App value |
|---|---|
| `uid` | Discovered door UID |
| `name` | Initial app-provided name |
| `push` | 1 |
| `device` | Selected device type |
| `product` | Selected product |
| `configure_mode` | Selected mode |
| `time_zone` | App timezone helper result |
| `synchro` | 1 |
| `longitude`, `latitude` | Location strings, or empty when unavailable |

`r.d()` builds this body and calls `ApiInterface.q0()` [J8].
The app handles retry and ownership-result cases, including already added to
self/others; QR handling also includes regional restrictions. Seeing a success
response in a mock does not prove the door would authorize subsequent sessions.

Renaming is another authenticated call:
`POST v1/api/user/update/device`, with `uid` and `name`.

## 4. QR provisioning

Unlike the AP/BLE flow, the QR route requests a server-side pairing transaction
**before** displaying the QR image [J9,J8]:

1. Authenticated `POST v1/api/user/app/qr/code` with device, name, timezone,
   synchro, configure_mode, product, push, longitude, and latitude.
2. Store the returned pairing key.
3. Display JSON containing the Wi-Fi details, key, and region.
4. Poll authenticated `GET v1/api/user/device/qr/code/list` with the pairing-key
   query field `user_key`.
5. Finish when the backend reports `success` or `added to myself`; otherwise
   handle ownership, restriction, expiry, or retry results.

QR JSON fields:

| Field | Meaning |
|---|---|
| `s` | SSID, or Base64 SSID for the non-ASCII branch |
| `e` | 0 for direct SSID, 1 for Base64 |
| `p` | Wi-Fi password |
| `t` | App's Wi-Fi capabilities value, or 0 for empty password |
| `k` | Cloud-issued pairing key |
| `a` | Numeric provisioning-region code |

The app does not put a configurable server URL into this QR payload.
Generating a visually valid QR is therefore not enough to complete the
unmodified cloud pairing flow.

## 5. Region selection, not custom server selection

The app's `config_local_server_id` preference means “locally stored choice of
vendor region,” **not** “local-only server.” `G1.a.a()` converts this ID into
the code sent to the door [S1,J10]:

| Region | App server ID | Provisioning code | App REST host | Native IoT host | App push host |
|---|---:|---:|---|---|---|
| China/default | 0 | 1 | `chniot.lancens.com:6448` | `chniotd.lancens.com` | `chnpush.lancens.com` |
| USA | 1 | 3 | `usaiot.lancens.com:6448` | `usaiotd.lancens.com` | `usapush.lancens.com` |
| Europe | 2 | 2 | `euriot.lancens.com:6448` | `euriotd.lancens.com` | `eurpush.lancens.com` |
| Test/show | 3 | 4 | `push.lancens.com:6448` | `push.lancens.com` | `push.lancens.com` |

The app model also has hard-coded debug routes. Its apparently flexible
`g(String)` constructor maps recognized hostnames to fixed endpoints and
defaults unrecognized strings to China; it is not a generic custom-host setter.
`f.z(String)` changing the app's region model is not a command that changes
the door's server.

**No arbitrary-server field was found in the traced AP, BLE, or QR builders.**
Whether undocumented firmware commands or another firmware build support one
remains unknown.

## Evidence map

Paths beginning `sources/` are relative to the locally retained
`apk-analysis/jadx/`; `smali_classes2/` is relative to
`apk-analysis/apktool/`. Decompiled source and APK binaries are not copied into
this repository. Line references apply to this saved decompilation.

| ID | Source / location |
|---|---|
| J1 | `sources/com/lancens/wxdoorbell/view/ConfiguringDeviceActivity.java:180`, `handlerNext` |
| J2 | `sources/com/lancens/api/LANUDP.java:13`, constants, JSON parser, native declarations |
| J3 | `sources/com/lancens/wxdoorbell/apconfig/ConfigWiFiActivity.java:92`, `:193`, `:292`, `:508`, `:680`, `:772`, `:878` |
| J4 | `sources/com/lancens/wxdoorbell/bleconfig/i.java:245`, `:600`, `:668`, `:1034`, `:1125`, `:1287` (`BleManager`) |
| J5 | `sources/com/lancens/wxdoorbell/bleconfig/a.java:6` (`BleCommand`) |
| J6 | `sources/com/lancens/wxdoorbell/bleconfig/o.java:35` (`WiFiRequest`) |
| J7 | `sources/com/lancens/wxdoorbell/bleconfig/BleConfigActivity.java:123`, `:456`, `:520`, `:532`, `:1014` |
| J8 | `sources/com/lancens/wxdoorbell/api/a.java:223`, `:229`, `:292`, `:338`, `:365`; `api/r.java:839`, `:964`, `:1084`, `:1330`, `:1534`; `apconfig/g.java:11`; `sources/com/lancens/libpush/model/PushInfo.java:38` |
| J9 | `sources/com/lancens/wxdoorbell/qrcodeconfig/QrConfigThirdActivity.java:239`, `:252`, `:333`, `:401`, `:659` |
| J10 | `sources/com/lancens/wxdoorbell/api/f.java:85`, `:208`, `:214`; `api/g.java:29`, `:96` |
| S1 | `smali_classes2/G1/a.smali:7` (`ConfigUtil.a`), exact region conversion |
| S2 | `smali_classes2/M1/c.smali:559`, exact command GATT service/characteristic |

Native references are ELF virtual symbol addresses in
`native-libs/arm64-v8a/libIVIEWSLANUDP.so`. Ghidra used image base `0x100000`
(e.g. ELF `0x2be08` is Ghidra `0x12be08`).

| ID | Symbol and ELF address | Finding |
|---|---|---|
| N1 | `Java_com_lancens_api_LANUDP_startSearch` @ `0x25a3c`; `sendDistributionCmd` @ `0x25d4c` | Envelope fields, initial index, payload copy |
| N2 | `broadcastSearch` @ `0x2be08` | UDP 5000, broadcast, nested-object formatting |
| N3 | `searchDeviceEx` @ `0x2bfac`; `searchDevice` @ `0x2c09c` | /24-style unicast sweep and UDP port |
| N4 | `createRecvThread` @ `0x2bc94`; `initSearch` @ `0x2c1e4` | Ephemeral receive port, local interface |
| N5 | `processResponseSearchDevice` @ `0x2bbb0` | Echo of configuration-status responses |

Address-based exports are retained under local
`apk-analysis/ghidra-decompiled/lanudp-pairing-core/` and
`lanudp-pairing-socket/`; JNI exports under `lanudp-pairing/`.
Address-based exports avoid name collisions between actual functions and PLT
thunks in the broad name-based export.

Artifact SHA-256:

- APK: `52e5a67a15b3dffe1333f9e1e1c785336869846109dc94efdafcafb1ef4d2c3b`
- ARM64 LANUDP library: `46ca861ea1c0880b516f96d0e068aebc708bb6362e5df2e8c0f6412e34be9d71`
