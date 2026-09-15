//! Independent DWG fixture for testing LibreDWG's UTF-8 DXF text boundaries.
use acadrust::{CadDocument, DxfVersion, DwgWriter, EntityType, MText, Vector3};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 3 { return Err("usage: text-fixture UTF8_LINES OUTPUT_DWG".into()); }
    let source = std::fs::read_to_string(&args[1])?;
    let mut doc = CadDocument::with_version(DxfVersion::AC1032);
    for (index, line) in source.lines().enumerate() {
        doc.add_entity(EntityType::MText(MText::with_value(
            line, Vector3::new(0.0, index as f64, 0.0),
        )))?;
    }
    std::fs::write(&args[2], DwgWriter::write_to_vec(&doc)?)?;
    println!("wrote {} text entities", doc.entity_count());
    Ok(())
}
