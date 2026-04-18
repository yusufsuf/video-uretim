-- Kling element cache TTL — 25 gün sonra yeniden yarat
-- Kling elementleri ~30 gün sonra sunucudan silinebiliyor; 25 gün eşiğiyle
-- erkenden yenileyerek kullanıcıya "element not found" hatası düşmesini engelleriz.

ALTER TABLE public.library_items
  ADD COLUMN IF NOT EXISTS kling_element_created_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS library_items_kling_element_created_at_idx
  ON public.library_items(kling_element_created_at)
  WHERE kling_element_id IS NOT NULL;

-- Mevcut cached element'lar için created_at'i updated_at / created_at'e set et
-- (henüz expire etmediyse yeniden yaratmaya gerek yok, süreç sayacı başlasın)
UPDATE public.library_items
  SET kling_element_created_at = COALESCE(updated_at, created_at)
  WHERE kling_element_id IS NOT NULL
    AND kling_element_created_at IS NULL;
