import datetime, os
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives.serialization import pkcs12

OUT = "mtls"
PASS = b"lanT2026"

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([
    x509.NameAttribute(NameOID.COMMON_NAME, "lan-transfer-client"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LanTransfer"),
])
now = datetime.datetime.now(datetime.timezone.utc)
cert = (x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .sign(key, hashes.SHA256()))

cert_pem = cert.public_bytes(serialization.Encoding.PEM)
key_pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.TraditionalOpenSSL,
                            serialization.NoEncryption())

open(os.path.join(OUT, "client-cert.pem"), "wb").write(cert_pem)
open(os.path.join(OUT, "client-key.pem"), "wb").write(key_pem)

# Android 端用的 PKCS12（含私钥+证书）
p12 = pkcs12.serialize_key_and_certificates(
    b"lan-transfer-client", key, cert, None,
    serialization.BestAvailableEncryption(PASS))
open(os.path.join(OUT, "client.p12"), "wb").write(p12)

fp = ":".join("%02X" % b for b in cert.fingerprint(hashes.SHA256()))
print("client cert fingerprint:", fp)
print("files:", os.listdir(OUT))
print("p12 size:", len(p12))
