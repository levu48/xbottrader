import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';

const KEY_LEN = 32;
const IV_LEN = 12;
const TAG_LEN = 16;
const VERSION = 1;

export interface EncryptedEnvelope {
  v: number;
  dek_iv: string;
  dek_ct: string;
  data_iv: string;
  data_ct: string;
}

export class EnvelopeCipher {
  readonly #kek: Buffer;

  constructor(kekBase64: string) {
    const kek = Buffer.from(kekBase64, 'base64');
    if (kek.length !== KEY_LEN) {
      throw new Error(`KEK must be 32 bytes (got ${kek.length})`);
    }
    this.#kek = kek;
  }

  static fromEnv(envVar = 'XBT_KEK'): EnvelopeCipher {
    const raw = process.env[envVar];
    if (!raw) throw new Error(`${envVar} env var not set`);
    return new EnvelopeCipher(raw);
  }

  encrypt(plaintext: string): EncryptedEnvelope {
    const dek = randomBytes(KEY_LEN);

    const dekIv = randomBytes(IV_LEN);
    const dekCipher = createCipheriv('aes-256-gcm', this.#kek, dekIv);
    const dekCt = Buffer.concat([dekCipher.update(dek), dekCipher.final(), dekCipher.getAuthTag()]);

    const dataIv = randomBytes(IV_LEN);
    const dataCipher = createCipheriv('aes-256-gcm', dek, dataIv);
    const dataCt = Buffer.concat([
      dataCipher.update(plaintext, 'utf8'),
      dataCipher.final(),
      dataCipher.getAuthTag(),
    ]);

    return {
      v: VERSION,
      dek_iv: dekIv.toString('base64'),
      dek_ct: dekCt.toString('base64'),
      data_iv: dataIv.toString('base64'),
      data_ct: dataCt.toString('base64'),
    };
  }

  decrypt(env: EncryptedEnvelope): string {
    if (env.v !== VERSION) {
      throw new Error(`Unsupported envelope version: ${env.v}`);
    }

    const dek = aesGcmDecrypt(this.#kek, base64(env.dek_iv), base64(env.dek_ct));
    return aesGcmDecrypt(dek, base64(env.data_iv), base64(env.data_ct)).toString('utf8');
  }

  static serialize(env: EncryptedEnvelope): string {
    return JSON.stringify(env);
  }

  static parse(s: string): EncryptedEnvelope {
    const obj = JSON.parse(s) as Partial<EncryptedEnvelope>;
    if (
      typeof obj.v !== 'number' ||
      typeof obj.dek_iv !== 'string' ||
      typeof obj.dek_ct !== 'string' ||
      typeof obj.data_iv !== 'string' ||
      typeof obj.data_ct !== 'string'
    ) {
      throw new Error('Invalid envelope shape');
    }
    return obj as EncryptedEnvelope;
  }
}

function base64(s: string): Buffer {
  return Buffer.from(s, 'base64');
}

function aesGcmDecrypt(key: Buffer, iv: Buffer, ctWithTag: Buffer): Buffer {
  if (ctWithTag.length < TAG_LEN) throw new Error('Ciphertext shorter than auth tag');
  const ct = ctWithTag.subarray(0, ctWithTag.length - TAG_LEN);
  const tag = ctWithTag.subarray(ctWithTag.length - TAG_LEN);
  const dec = createDecipheriv('aes-256-gcm', key, iv);
  dec.setAuthTag(tag);
  return Buffer.concat([dec.update(ct), dec.final()]);
}
