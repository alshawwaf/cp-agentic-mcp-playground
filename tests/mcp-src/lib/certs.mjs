// Test certificates, generated fresh at test time (nothing is committed, nothing expires in the repo).
// Pure Node: builds X.509 v3 certificates in DER with node:crypto, so the test container needs no
// openssl and no network. Every key is a throwaway RSA-2048 key that lives only in the test's /tmp.
//
//   node lib/certs.mjs <dir>     writes <name>.pem + <name>.key for every certificate below
//
// ca       "Lab Test CA" (CA:TRUE)              evil-ca  "Untrusted CA" (CA:TRUE), trusted by nobody
// server   mgmt.lab.test + IP 127.0.0.1/.2, issued by ca (the fake SMS / Gaia API)
// self     sms.lab.test, self-signed, no basicConstraints (a typical self-signed SMS certificate)
// leaf     sms2.lab.test, self-signed end-entity (CA:FALSE), as real SMS / Gaia certificates often are
// te-fake  te-api.checkpoint.com, issued by ca (the loopback fake Threat Emulation cloud)
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

// ---------------------------------------------------------------- minimal DER encoder
const derLen = n => {
  if (n < 0x80) return Buffer.from([n]);
  const bytes = [];
  for (let x = n; x > 0; x = Math.floor(x / 256)) bytes.unshift(x & 0xff);
  return Buffer.from([0x80 | bytes.length, ...bytes]);
};
const tlv = (tag, ...parts) => {
  const body = Buffer.concat(parts);
  return Buffer.concat([Buffer.from([tag]), derLen(body.length), body]);
};
const seq = (...p) => tlv(0x30, ...p);
const set = (...p) => tlv(0x31, ...p);
const integer = buf => tlv(0x02, buf[0] & 0x80 ? Buffer.concat([Buffer.from([0]), buf]) : buf);
const oid = dotted => {
  const p = dotted.split('.').map(Number);
  const out = [40 * p[0] + p[1]];
  for (const v of p.slice(2)) {
    const b = [v & 0x7f];
    for (let x = Math.floor(v / 128); x > 0; x = Math.floor(x / 128)) b.unshift(0x80 | (x & 0x7f));
    out.push(...b);
  }
  return tlv(0x06, Buffer.from(out));
};
const NULL = Buffer.from([0x05, 0x00]);
const utf8 = s => tlv(0x0c, Buffer.from(s, 'utf8'));
const octets = b => tlv(0x04, b);
const bits = (b, unused = 0) => tlv(0x03, Buffer.from([unused]), b);
const bool = v => tlv(0x01, Buffer.from([v ? 0xff : 0x00]));
const explicit = (n, ...p) => tlv(0xa0 | n, ...p);
const utcTime = d => tlv(0x17, Buffer.from(d.toISOString().replace(/[-:T]/g, '').slice(2, 14) + 'Z'));

const SHA256_RSA = seq(oid('1.2.840.113549.1.1.11'), NULL);
const name = cn => seq(set(seq(oid('2.5.4.3'), utf8(cn))));
const ext = (id, critical, value) => seq(oid(id), ...(critical ? [bool(true)] : []), octets(value));
const san = entries => seq(...entries.map(e => (/^\d+\.\d+\.\d+\.\d+$/.test(e)
  ? tlv(0x87, Buffer.from(e.split('.').map(Number)))   // iPAddress
  : tlv(0x82, Buffer.from(e, 'ascii')))));              // dNSName
const keyId = pub => crypto.createHash('sha1').update(pub.export({ type: 'spki', format: 'der' })).digest();

// ---------------------------------------------------------------- certificate profiles
function issue({ cn, sans = [], profile, issuer }) {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
  const signer = issuer || { cn, privateKey, publicKey };
  const serial = crypto.randomBytes(12);
  serial[0] = (serial[0] & 0x7f) | 0x01;
  const now = Date.now();
  const exts = [];
  if (profile === 'ca') {
    exts.push(ext('2.5.29.19', true, seq(bool(true))));                  // basicConstraints CA:TRUE
    exts.push(ext('2.5.29.15', true, bits(Buffer.from([0x06]), 1)));     // keyCertSign, cRLSign
  } else if (profile === 'server') {
    exts.push(ext('2.5.29.19', false, seq()));                           // basicConstraints CA:FALSE
    exts.push(ext('2.5.29.15', true, bits(Buffer.from([0xa0]), 5)));     // digitalSignature, keyEncipherment
    exts.push(ext('2.5.29.37', false, seq(oid('1.3.6.1.5.5.7.3.1'))));   // serverAuth
  }                                                                      // profile 'plain': SAN only
  if (sans.length) exts.push(ext('2.5.29.17', false, san(sans)));
  exts.push(ext('2.5.29.14', false, octets(keyId(publicKey))));          // subjectKeyIdentifier
  if (issuer) exts.push(ext('2.5.29.35', false, seq(tlv(0x80, keyId(issuer.publicKey))))); // authorityKeyIdentifier
  const tbs = seq(
    explicit(0, integer(Buffer.from([2]))),                              // v3
    integer(serial),
    SHA256_RSA,
    name(signer.cn),
    seq(utcTime(new Date(now - 3600e3)), utcTime(new Date(now + 2 * 86400e3))), // valid for this test run only
    name(cn),
    publicKey.export({ type: 'spki', format: 'der' }),
    explicit(3, seq(...exts)),
  );
  const der = seq(tbs, SHA256_RSA, bits(crypto.sign('sha256', tbs, signer.privateKey)));
  const pem = `-----BEGIN CERTIFICATE-----\n${der.toString('base64').match(/.{1,64}/g).join('\n')}\n-----END CERTIFICATE-----\n`;
  // Self-check with Node's own X.509 parser, so a broken encoding fails here and not inside a TLS test.
  const x = new crypto.X509Certificate(pem);
  if (!x.verify(signer.publicKey)) throw new Error(`certificate ${cn}: signature does not verify`);
  return { cn, pem, key: privateKey.export({ type: 'pkcs8', format: 'pem' }), privateKey, publicKey };
}

export function generateCerts(dir) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const ca = issue({ cn: 'Lab Test CA', profile: 'ca' });
  const certs = {
    ca,
    'evil-ca': issue({ cn: 'Untrusted CA', profile: 'ca' }),
    server: issue({ cn: 'mgmt.lab.test', sans: ['mgmt.lab.test', '127.0.0.1', '127.0.0.2'], profile: 'server', issuer: ca }),
    self: issue({ cn: 'sms.lab.test', sans: ['sms.lab.test'], profile: 'plain' }),
    leaf: issue({ cn: 'sms2.lab.test', sans: ['sms2.lab.test'], profile: 'server' }),
    'te-fake': issue({ cn: 'te-api.checkpoint.com (LAB TEST ONLY - loopback fake)', sans: ['te-api.checkpoint.com'], profile: 'server', issuer: ca }),
  };
  for (const [file, c] of Object.entries(certs)) {
    fs.writeFileSync(path.join(dir, `${file}.pem`), c.pem);
    fs.writeFileSync(path.join(dir, `${file}.key`), c.key, { mode: 0o600 });
  }
  return Object.keys(certs);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const dir = process.argv[2];
  if (!dir) {
    console.error('usage: node certs.mjs <dir>');
    process.exit(2);
  }
  console.log(`test certificates (valid 2 days, keys never leave this container): ${generateCerts(dir).join(', ')}`);
}
