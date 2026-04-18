-- profiles tablosu — Supabase Auth entegrasyonu için kullanıcı profilleri
-- Hata: "column profiles.id does not exist" (42703) — tablo var ama id kolonu eksik.
-- Bu migration hem eksik kolonları hem eksik tabloyu idempotent şekilde onarır.

CREATE TABLE IF NOT EXISTS public.profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email TEXT,
  full_name TEXT,
  role TEXT NOT NULL DEFAULT 'user',
  approved BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tablo önceden farklı şemayla oluşturulmuşsa eksik kolonları ekle.
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS id UUID;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS full_name TEXT;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'user';
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS approved BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- Eğer "user_id" gibi eski bir foreign key kolonu varsa id'ye taşı.
DO $$
BEGIN
  IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'profiles' AND column_name = 'user_id'
  ) THEN
    UPDATE public.profiles SET id = user_id WHERE id IS NULL AND user_id IS NOT NULL;
  END IF;
END $$;

-- id kolonuna PRIMARY KEY constraint ve auth.users FK ekle (yoksa).
DO $$
BEGIN
  IF NOT EXISTS (
      SELECT 1 FROM information_schema.table_constraints
      WHERE table_schema = 'public' AND table_name = 'profiles' AND constraint_type = 'PRIMARY KEY'
  ) THEN
    ALTER TABLE public.profiles ADD PRIMARY KEY (id);
  END IF;
EXCEPTION WHEN others THEN
  -- null id'ler varsa PK eklenemez; bu kayıtlar elle temizlenmeli
  RAISE NOTICE 'Primary key eklenemedi: %', SQLERRM;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
      SELECT 1 FROM information_schema.table_constraints
      WHERE table_schema = 'public' AND table_name = 'profiles'
        AND constraint_type = 'FOREIGN KEY' AND constraint_name = 'profiles_id_fkey'
  ) THEN
    ALTER TABLE public.profiles
      ADD CONSTRAINT profiles_id_fkey FOREIGN KEY (id) REFERENCES auth.users(id) ON DELETE CASCADE;
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE 'FK eklenemedi: %', SQLERRM;
END $$;

CREATE INDEX IF NOT EXISTS profiles_role_idx ON public.profiles(role);
CREATE INDEX IF NOT EXISTS profiles_approved_idx ON public.profiles(approved);
