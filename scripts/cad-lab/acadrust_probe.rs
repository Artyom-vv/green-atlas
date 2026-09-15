// Isolated strict-read experiment. Never write beside the original drawing.
use acadrust::{DwgReader, DxfWriter};
use std::{collections::BTreeMap, env, time::Instant};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.len() != 3 { return Err("usage: probe source.dwg separate-output.dxf".into()); }
    if std::path::Path::new(&args[2]).exists() { return Err("output exists".into()); }
    let started = Instant::now();
    let mut reader = DwgReader::from_file(&args[1])?;
    let outcome = reader.read_with_stats()?;
    let doc = outcome.document;
    println!("READ_SECONDS {}", started.elapsed().as_secs_f64());
    println!("READ_STATS {:?}", outcome.stats);
    let mut counts = BTreeMap::new();
    for entity in doc.entities() {
        *counts.entry(entity.as_entity().entity_type()).or_insert(0usize) += 1;
    }
    println!("ENTITIES {:?}", counts);
    for entity in doc.entities() {
        if let acadrust::entities::EntityType::Region(region) = entity {
            let prefix = format!("{}.{}", args[2], entity.common().handle.value());
            std::fs::write(format!("{}.sab", prefix), &region.acis_data.sab_data)?;
            match acadrust::entities::acis::SabReader::read(&region.acis_data.sab_data) {
                Ok(sat) => {
                    println!("REGION {} SAB_RECORDS {}", entity.common().handle.value(), sat.records.len());
                    std::fs::write(format!("{}.sat", prefix), sat.to_sat_string())?;
                }
                Err(error) => println!("REGION {} SAB_ERROR {:?}", entity.common().handle.value(), error),
            }
        }
    }
    DxfWriter::new(&doc).write_to_file(&args[2])?;
    println!("TOTAL_SECONDS {}", started.elapsed().as_secs_f64());
    Ok(())
}
