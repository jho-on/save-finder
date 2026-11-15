-- +goose Up
CREATE TABLE user_key_metadata (
    user_id         uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    salt            bytea  NOT NULL,
    wrapped_dek     bytea  NOT NULL,
    dek_nonce       bytea  NOT NULL,
    kdf_ops_limit   bigint NOT NULL,
    kdf_mem_limit   bigint NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- +goose Down
DROP TABLE user_key_metadata;
