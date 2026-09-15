/* Evidence harness for the downstream LibreDWG ACDS patch. GPL-3.0-or-later.
   Uses the actual full drawing plus the independently decompressed datastore.
   No output CAD file and no source mutation. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>
#include "dwg.h"
#include "bits.h"
#include "decode.h"
#include "out_dxf.h"

static void clear_bodies(Dwg_Data *dwg) {
  for (BITCODE_BL i = 0; i < dwg->num_objects; i++) {
    Dwg_Object *o = &dwg->object[i];
    if (dwg_obj_is_3dsolid(o) && o->tio.entity->has_ds_data) {
      Dwg_Entity_3DSOLID *s = o->tio.entity->tio._3DSOLID;
      free(s->acis_data); s->acis_data = NULL; s->sab_size = 0;
    }
  }
}

static unsigned char *read_bytes(const char *path, size_t *size) {
  FILE *f = fopen(path, "rb");
  unsigned char *data;
  if (!f) return NULL;
  fseek(f, 0, SEEK_END); *size = (size_t)ftell(f); rewind(f);
  data = (unsigned char *)malloc(*size);
  if (!data || fread(data, 1, *size, f) != *size) { free(data); data = NULL; }
  fclose(f); return data;
}

static int verify(Dwg_Data *dwg, const char *dir) {
  unsigned count = 0;
  for (BITCODE_BL i = 0; i < dwg->num_objects; i++) {
    Dwg_Object *o = &dwg->object[i];
    if (dwg_obj_is_3dsolid(o) && o->tio.entity->has_ds_data) {
      Dwg_Entity_3DSOLID *s = o->tio.entity->tio._3DSOLID;
      char path[4096]; size_t size; unsigned char *expected;
      snprintf(path, sizeof(path), "%s/%" PRIX64 ".sab", dir, (uint64_t)o->handle.value);
      expected = read_bytes(path, &size);
      if (!expected || !s->acis_data || size != s->sab_size || memcmp(expected, s->acis_data, size)) {
        free(expected); return 0;
      }
      free(expected); count++;
    }
  }
  printf("verified modeler bodies: %u\n", count);
  return count > 0;
}

int main(int argc, char **argv) {
  Dwg_Data dwg = {0}; Bit_Chain raw = {0};
  Dwg_AcDs_DataIndex_Entry *saved;
  size_t bytes; BITCODE_BL count, physical_entry = UINT32_MAX;
  int code;
  if (argc != 4) return 2;
  code = dwg_read_file(argv[1], &dwg);
  if (code >= DWG_ERR_CRITICAL || !verify(&dwg, argv[3])) return 3;
  puts("PASS full source bytes by handle");
  raw.chain = read_bytes(argv[2], &raw.size);
  if (!raw.chain) return 4;
  raw.version = dwg.header.version;
  count = dwg.acds.datidx.num_entries;
  bytes = count * sizeof(*saved);
  saved = (Dwg_AcDs_DataIndex_Entry *)malloc(bytes);
  if (!saved) return 5;
  memcpy(saved, dwg.acds.datidx.entries, bytes);
  for (BITCODE_BL i = 0; i < count; i++) dwg.acds.datidx.entries[i] = saved[count - 1 - i];
  clear_bodies(&dwg);
  if (dwg_decode_acds_sab(&raw, &dwg) || !verify(&dwg, argv[3])) return 6;
  puts("PASS reordered active index preserves owners");
  memcpy(dwg.acds.datidx.entries, saved, bytes);
  for (BITCODE_BL i = 0; i < count; i++) {
    Dwg_AcDs_DataIndex_Entry e = saved[i];
    Bit_Chain cursor = raw; Dwg_Object *object; BITCODE_RLL handle;
    if (!e.segidx) continue;
    cursor.byte = (size_t)dwg.acds.segidx[e.segidx].offset + 48 + e.offset + 8;
    handle = bit_read_RLL(&cursor); object = dwg_resolve_handle_silent(&dwg, handle);
    if (object && dwg_obj_is_3dsolid(object) && object->tio.entity->has_ds_data) { physical_entry = i; break; }
  }
  if (physical_entry == UINT32_MAX) return 7;
  dwg.acds.datidx.entries[physical_entry].segidx = 0;
  clear_bodies(&dwg);
  if (!dwg_decode_acds_sab(&raw, &dwg)) return 8;
  puts("PASS missing active owner rejected");
  {
    Bit_Chain output = {0};
    output.version = raw.version; output.from_version = raw.version;
    output.fh = tmpfile();
    if (!output.fh) return 13;
    if (!dwg_write_dxf(&output, &dwg) || ftell(output.fh) != 0) return 14;
    fclose(output.fh);
    puts("PASS incomplete modeler export rejected before output");
  }
  memcpy(dwg.acds.datidx.entries, saved, bytes);
  dwg.acds.datidx.entries[(physical_entry + 1) % count] = saved[physical_entry];
  clear_bodies(&dwg);
  if (!dwg_decode_acds_sab(&raw, &dwg)) return 9;
  puts("PASS duplicate active owner rejected");
  memcpy(dwg.acds.datidx.entries, saved, bytes);
  {
    Dwg_AcDs_DataIndex_Entry e = saved[physical_entry];
    size_t seg = (size_t)dwg.acds.segidx[e.segidx].offset;
    Bit_Chain cursor = raw; size_t pos; unsigned char original[4];
    cursor.byte = seg + 48 + e.offset + 16;
    pos = seg + (size_t)dwg.acds.segments[e.segidx].objdata_algn_offset * 16 + bit_read_RL(&cursor);
    memcpy(original, raw.chain + pos, 4); memset(raw.chain + pos, 0xff, 4);
    clear_bodies(&dwg);
    if (!dwg_decode_acds_sab(&raw, &dwg)) return 10;
    memcpy(raw.chain + pos, original, 4);
    puts("PASS oversized payload rejected");
  }
  {
    size_t original = raw.size;
    raw.size = 64;
    clear_bodies(&dwg);
    if (!dwg_decode_acds_sab(&raw, &dwg)) return 11;
    raw.size = original;
    puts("PASS truncated datastore rejected");
  }
  clear_bodies(&dwg);
  if (dwg_decode_acds_sab(&raw, &dwg) || !verify(&dwg, argv[3])) return 12;
  puts("PASS recovery after rejected input");
  free(saved); free(raw.chain); dwg_free(&dwg);
  return 0;
}
