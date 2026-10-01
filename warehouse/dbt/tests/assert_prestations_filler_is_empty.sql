-- FILLER takes the empty field after each line's trailing `;` (ADR 0012). A value
-- there means the file has a field the DDL doesn't declare.
select FILLER
from {{ source('damir', 'PRESTATIONS') }}
where FILLER is not null
