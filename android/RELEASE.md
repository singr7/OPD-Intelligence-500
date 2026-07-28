# Android release signing

The application ID is permanently `ai.radpretation.opd`. Every update must use the
same signing key and a higher `versionCode`.

Signing inputs are read only from the controlled workstation environment or its
ignored `android/local.properties`:

```text
OPD_SIGNING_KEYSTORE / opdSigningKeystore
OPD_SIGNING_STORE_PASSWORD / opdSigningStorePassword
OPD_SIGNING_KEY_ALIAS / opdSigningKeyAlias
OPD_SIGNING_KEY_PASSWORD / opdSigningKeyPassword
```

Create the production key on the signing workstation, not a server. Before the
first public APK, make two encrypted offline backups on separate physical media,
store them in separate controlled locations, and test restoring one copy. Record
the custodians and certificate SHA-256 outside Git. Losing or replacing the key
prevents Android from installing future updates over the existing app.

Build from a clean committed tree:

```bash
android/scripts/build-signed-release.sh
```

The script runs release unit tests, R8/resource shrinking, the 15 MB gate, and
`apksigner verify --verbose --print-certs`; then emits the APK, checksum, report,
and JSON manifest under the ignored `android/release/` directory. Do not copy a
keystore, password, or signing report containing workstation paths to a server.
