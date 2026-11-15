-- +goose Up
CREATE TABLE library_items (
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_key    text NOT NULL,          -- "platform:external_id"; opaque to the server except for upsert semantics
    payload     bytea NOT NULL,         -- nonce (24B) || XChaCha20-Poly1305 ciphertext of the item JSON
    transcript  bytea,                  -- nonce || ciphertext; kept separate so lists stay small
    thumb       bytea,                  -- nonce || ciphertext; kept separate so lists stay small
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, item_key)
);

CREATE INDEX library_items_user_updated_idx ON library_items (user_id, updated_at, item_key);

-- +goose Down
DROP TABLE library_items;
